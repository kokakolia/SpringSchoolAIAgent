import httpx
import pytest

from src import memory, plans
from src.config import settings
from src.memory_schemas import CreateProject, ProjectState, UpdateProject
from src.plan_schemas import ChangeTask, LoseTime, PlanProposal, PlanTask, ProposePlan
from src.planner import validate_plan
from src.server import app
from tests.test_plans import Client, accepted, operation, proposal, state


def test_all_deferred_is_not_a_feasible_plan_even_without_constraints():
    value = proposal()
    for task in value.tasks:
        task.decision, task.tradeoff = "defer", "Later"
    value.buffer_minutes = 0
    facts = {**state(), "constraints": [], "available_minutes": 0}
    result = validate_plan(value, facts, {})
    assert not result["valid"] and result["next_task_id"] is None
    assert "no_active_work" in {error["code"] for error in result["errors"]}


async def test_all_deferred_cannot_be_accepted():
    p = await memory.create_project(
        CreateProject(
            title="No work",
            state=ProjectState.model_validate(
                {**state(), "constraints": [], "available_minutes": 0}
            ),
        )
    )
    value = proposal()
    for task in value.tasks:
        task.decision, task.tradeoff = "defer", "Later"
    value.buffer_minutes = 0
    draft = await plans.propose(
        p["id"], ProposePlan(**operation(0).model_dump(), text="Plan"), Client(value)
    )
    with pytest.raises(memory.MemoryError) as error:
        await plans.accept(p["id"], draft["plan"]["id"], operation(1))
    assert error.value.status == 422


async def test_time_loss_overrun_is_preserved_when_completed_task_is_reopened():
    pid, _ = await accepted()
    await plans.change_task(
        pid, "api", ChangeTask(**operation(2).model_dump(), done=True, actual_minutes=40)
    )
    await plans.lose_time(pid, LoseTime(**operation(3).model_dump(), minutes=220))
    assert (await memory.get_project(pid))["state"]["available_minutes"] == 0
    await plans.change_task(pid, "api", ChangeTask(**operation(4).model_dump(), done=False))
    p = await memory.get_project(pid)
    assert p["state"]["available_minutes"] == 20
    assert p["time_budget"] == {
        "allocated_minutes": 240,
        "lost_minutes": 220,
        "charged_minutes": 0,
        "overrun_minutes": 0,
    }


async def test_editing_goal_does_not_forgive_hidden_time_debt():
    pid, _ = await accepted()
    await plans.change_task(pid, "api", ChangeTask(**operation(2).model_dump(), done=True))
    await plans.lose_time(pid, LoseTime(**operation(3).model_dump(), minutes=220))
    p = await memory.get_project(pid)
    await memory.update_project(
        pid,
        UpdateProject(
            base_revision=4,
            state=ProjectState.model_validate({**p["state"], "goal": "More precise goal"}),
        ),
    )
    await plans.change_task(pid, "api", ChangeTask(**operation(5).model_dump(), done=False))
    assert (await memory.get_project(pid))["state"]["available_minutes"] == 20


async def test_existing_session_remains_usable_after_global_failed_login_limit(monkeypatch):
    from pydantic import SecretStr

    password = "test-only-owner-password"
    monkeypatch.setattr(settings, "app_password", SecretStr(password))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.99", 1)),
        base_url="https://app.example",
    ) as owner:
        assert (await owner.get("/api/projects", auth=("owner", password))).status_code == 200
        for i in range(10):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=(f"203.0.113.{i + 1}", 1)),
                base_url="https://app.example",
            ) as attacker:
                for _ in range(10):
                    response = await attacker.get("/api/projects", auth=("owner", "invalid"))
                    assert response.status_code in (401, 429)
        assert (await owner.get("/api/projects")).status_code == 200
        owner.cookies.clear()
        assert (await owner.get("/api/projects", auth=("owner", password))).status_code == 429


async def test_thirty_historical_tasks_can_be_archived_only_through_accepted_proposal():
    facts = {**state(), "constraints": [], "available_minutes": 1000}
    p = await memory.create_project(
        CreateProject(title="Thirty", state=ProjectState.model_validate(facts))
    )
    old = PlanProposal(
        summary="Old",
        buffer_minutes=0,
        tasks=[
            PlanTask(
                id=f"old_{i}",
                title=f"Old {i}",
                estimate_minutes=1,
                priority="must",
                decision="keep" if i == 0 else "defer",
                done_when="Done",
                reason="Needed",
                tradeoff="Later" if i else "",
            )
            for i in range(30)
        ],
    )
    initial = await plans.propose(
        p["id"], ProposePlan(**operation(0).model_dump(), text="Plan"), Client(old)
    )
    await plans.accept(p["id"], initial["plan"]["id"], operation(1))
    await plans.change_task(p["id"], "old_0", ChangeTask(**operation(2).model_dump(), done=True))
    new = PlanProposal(
        summary="New work and explicit archive",
        buffer_minutes=0,
        archive_task_ids=[task.id for task in old.tasks],
        tasks=[
            PlanTask(
                id="new_work",
                title="New",
                estimate_minutes=10,
                priority="must",
                decision="keep",
                done_when="Shipped",
                reason="New goal",
            )
        ],
    )
    draft = await plans.propose(
        p["id"],
        ProposePlan(**operation(3).model_dump(), text="Archive history and add work"),
        Client(new),
    )
    assert draft["plan"]["validation"]["valid"]
    assert (await plans.task_archive(p["id"]))["total"] == 0
    await plans.accept(p["id"], draft["plan"]["id"], operation(4))
    archived = await plans.task_archive(p["id"], limit=10)
    assert archived["total"] == 30 and len(archived["items"]) == 10
    assert len((await plans.task_archive(p["id"], offset=10, limit=30))["items"]) == 20
    board = await plans.workspace(p["id"])
    assert [task["id"] for task in board["accepted"]["proposal"]["tasks"]] == ["new_work"]
    from src import database
    from src.memory_models import TaskProgress

    async with database._get_session_maker()() as session:
        assert (await session.get(TaskProgress, (p["id"], "old_0"))).done
    repeated = new.model_copy(deep=True)
    repeated.archive_task_ids = []
    repeated.tasks[0].id = "old_0"
    from src.task_integrity import progress_map

    async with database._get_session_maker()() as session:
        historical_flags = await progress_map(session, p["id"], {"old_0"})
    checked = validate_plan(
        repeated, board["project"]["state"], historical_flags, board["accepted"]["proposal"]
    )
    assert "archived_id" in {e["code"] for e in checked["errors"]}


def test_active_or_pinned_tasks_cannot_be_archived():
    old = proposal()
    new = old.model_copy(deep=True)
    new.tasks.pop(0)
    new.archive_task_ids = ["api"]
    assert "bad_archive" in {
        e["code"] for e in validate_plan(new, state(), {}, old.model_dump())["errors"]
    }
    assert "bad_archive" in {
        e["code"]
        for e in validate_plan(
            new, state(), {"api": {"done": True, "pinned": True}}, old.model_dump()
        )["errors"]
    }


def test_zero_remaining_work_requires_user_confirmation_of_project_completion():
    value = proposal()
    flags = {task.id: {"done": True} for task in value.tasks}
    assert not validate_plan(value, state(), flags)["valid"]
    assert validate_plan(value, {**state(), "completed": True, "available_minutes": 0}, flags)[
        "valid"
    ]
