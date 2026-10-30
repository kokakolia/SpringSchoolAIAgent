import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src import database, history, memory, plans
from src.config import settings
from src.memory_models import ArchivedTask, Message, PlanVersion, RequirementProof, TaskProgress
from src.memory_schemas import ProjectState, SendMessage, UpdateProject
from src.plan_schemas import ChangeTask, PlanProposal, PlanTask, ProposePlan
from src.server import app
from tests.test_memory import FakeClient
from tests.test_plans import Client, accepted, operation, project, proposal


async def test_sessions_expire_and_password_change_revokes_existing_token(monkeypatch):
    from src import sessions
    from src.memory_models import AuthSession, utcnow

    monkeypatch.setattr(settings, "app_password", SecretStr("a-test-session-password"))
    token = await sessions.issue_session()
    assert await sessions.valid_session(token)
    monkeypatch.setattr(settings, "app_password", SecretStr("a-different-test-password"))
    await sessions.rotate_credentials(1)
    assert not await sessions.valid_session(token)
    monkeypatch.setattr(settings, "app_password", SecretStr("a-test-session-password"))
    await sessions.rotate_credentials(2)
    assert not await sessions.valid_session(token)
    token = await sessions.issue_session()
    async with database._get_session_maker()() as session:
        row = await session.get(AuthSession, sessions.digest(token))
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    assert not await sessions.valid_session(token)


async def test_password_verification_stops_at_limit_but_existing_session_survives(monkeypatch):
    from src import access

    monkeypatch.setattr(settings, "app_password", SecretStr("a-test-session-password"))
    monkeypatch.setattr(settings, "auth_attempts_per_minute", 1)
    original = access.verify_password
    calls = []

    def checked(user, password):
        calls.append(1)
        return original(user, password)

    monkeypatch.setattr(access, "verify_password", checked)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://app.example") as owner:
        login = await owner.post(
            "/auth/login", data={"username": "owner", "password": "a-test-session-password"}
        )
        assert login.status_code == 303
        cookie = login.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
        calls.clear()
        async with httpx.AsyncClient(
            transport=transport, base_url="https://app.example"
        ) as attacker:
            assert (await attacker.get("/api/projects", auth=("owner", "wrong"))).status_code == 401
            for guess in ("wrong", "wrong-again", "a-test-session-password"):
                assert (
                    await attacker.get("/api/projects", auth=("owner", guess))
                ).status_code == 429
        assert len(calls) == 1
        assert (await owner.get("/api/projects")).status_code == 200
        assert len(calls) == 1
        old_cookie = owner.cookies.get("__Host-deadline_session")
        assert (await owner.post("/auth/logout")).status_code == 303
        assert (await owner.get("/api/projects")).status_code == 401
        owner.cookies.set("__Host-deadline_session", old_cookie)
        assert (await owner.get("/api/projects")).status_code == 401


async def test_changed_requirement_does_not_inherit_completion_and_archive_keeps_original():
    pid, _ = await accepted()
    await plans.change_task(pid, "api", ChangeTask(**operation(2).model_dump(), done=True))
    await plans.change_task(pid, "ui", ChangeTask(**operation(3).model_dump(), done=True))
    before = await memory.get_project(pid)
    old_ref = before["requirements"][0]
    await memory.update_project(
        pid,
        UpdateProject(
            base_revision=4,
            state=ProjectState.model_validate(
                {**before["state"], "constraints": ["Шифрование данных при хранении"]}
            ),
        ),
    )
    after = await memory.get_project(pid)
    assert after["requirements"][0]["id"] == old_ref["id"]
    assert after["requirements"][0]["version"] == old_ref["version"] + 1
    assert not (await plans.workspace(pid))["current_validation"]["valid"]
    bad = await plans.propose(
        pid, ProposePlan(**operation(5).model_dump(), text="Plan"), Client(proposal())
    )
    assert not bad["plan"]["validation"]["valid"]
    with pytest.raises(memory.MemoryError):
        await plans.accept(pid, bad["plan"]["id"], operation(6))
    board = await plans.workspace(pid)
    revised = PlanProposal.model_validate(board["accepted"]["proposal"])
    revised.tasks = [task for task in revised.tasks if task.id != "ui"]
    revised.archive_task_ids = ["ui"]
    new_ref = after["requirements"][0]
    revised.tasks.append(
        PlanTask(
            id="encryption",
            title="Шифрование",
            estimate_minutes=10,
            priority="must",
            decision="keep",
            done_when="Данные зашифрованы",
            reason="Новое требование",
            requirement_refs=[{"id": new_ref["id"], "version": new_ref["version"]}],
        )
    )
    good = await plans.propose(
        pid, ProposePlan(**operation(6).model_dump(), text="Add new work"), Client(revised)
    )
    assert good["plan"]["validation"]["valid"]
    await plans.accept(pid, good["plan"]["id"], operation(7))
    async with database._get_session_maker()() as session:
        archived = await session.get(ArchivedTask, (pid, "ui"))
        assert archived.requirements[0]["text"] == "Нужен интерфейс"
        assert archived.requirements[0]["version"] == old_ref["version"]


async def test_legacy_done_task_remains_usable_without_claiming_current_requirements():
    from sqlalchemy import delete

    pid, _ = await accepted()
    await plans.change_task(pid, "api", ChangeTask(**operation(2).model_dump(), done=True))
    await plans.change_task(pid, "ui", ChangeTask(**operation(3).model_dump(), done=True))
    async with database._get_session_maker()() as session:
        head = await plans.head_plan(session, pid)
        legacy = dict(head.proposal)
        legacy["tasks"] = [
            {k: v for k, v in task.items() if k != "requirement_refs"} for task in legacy["tasks"]
        ]
        head.proposal, head.requirements = legacy, []
        await session.execute(delete(RequirementProof).where(RequirementProof.project_id == pid))
        await session.commit()
    assert not (await plans.workspace(pid))["current_validation"]["valid"]
    value = PlanProposal.model_validate(legacy)
    value.tasks[-1].constraint_refs = [1]
    draft = await plans.propose(
        pid, ProposePlan(**operation(4).model_dump(), text="Проверить требование"), Client(value)
    )
    assert draft["plan"]["validation"]["valid"]
    assert draft["plan"]["proposal"]["tasks"][1]["requirement_refs"] == []
    await plans.accept(pid, draft["plan"]["id"], operation(5))


async def test_archived_completed_dependent_blocks_reopening_and_detects_legacy_inconsistency():
    pid, _ = await accepted()
    await plans.change_task(pid, "api", ChangeTask(**operation(2).model_dump(), done=True))
    await plans.change_task(pid, "ui", ChangeTask(**operation(3).model_dump(), done=True))
    value = PlanProposal.model_validate((await plans.workspace(pid))["accepted"]["proposal"])
    value.tasks = [task for task in value.tasks if task.id != "ui"]
    value.archive_task_ids = ["ui"]
    draft = await plans.propose(
        pid, ProposePlan(**operation(4).model_dump(), text="Archive UI"), Client(value)
    )
    await plans.accept(pid, draft["plan"]["id"], operation(5))
    with pytest.raises(memory.MemoryError):
        await plans.change_task(pid, "api", ChangeTask(**operation(6).model_dump(), done=False))
    async with database._get_session_maker()() as session:
        row = await session.get(TaskProgress, (pid, "api"))
        row.done = False
        await session.commit()
    assert not (await plans.workspace(pid))["current_validation"]["valid"]
    with pytest.raises(memory.MemoryError):
        await plans.change_task(
            pid, "animation", ChangeTask(**operation(6).model_dump(), done=True)
        )


async def test_cross_api_request_collision_is_409_before_any_model_generation(monkeypatch):
    p = await project()
    request_id = uuid4()
    await memory.send_message(
        p["id"], SendMessage(request_id=request_id, base_revision=0, text="Hello"), FakeClient()
    )
    client = Client()
    for _ in range(2):
        with pytest.raises(memory.MemoryError) as error:
            await plans.propose(
                p["id"], ProposePlan(request_id=request_id, base_revision=1, text="Plan"), client
            )
        assert error.value.status == 409
    assert client.calls == 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        response = await api.post(
            f"/api/projects/{p['id']}/plans/propose",
            json={"request_id": str(request_id), "base_revision": 1, "text": "Plan"},
        )
        assert response.status_code == 409


async def test_workspace_and_message_history_are_bounded_at_three_hundred_versions():
    p = await project()
    async with database._get_session_maker()() as session:
        session.add_all(
            [
                PlanVersion(
                    project_id=p["id"],
                    request_id=str(uuid4()),
                    base_revision=0,
                    proposal=proposal().model_dump(),
                    validation={"valid": True, "padding": "x" * 12000},
                )
                for _ in range(300)
            ]
        )
        session.add_all(
            [
                Message(project_id=p["id"], request_id=str(uuid4()), role="user", text="x" * 1000)
                for _ in range(200)
            ]
        )
        await session.commit()
    board = await plans.workspace(p["id"])
    assert "plans" not in board and "events" not in board
    assert len(json.dumps(board).encode()) < 50000
    first = await history.page(p["id"], limit=30)
    second = await history.page(p["id"], offset=first["next_offset"], limit=30)
    assert len(first["items"]) == 30 and first["has_more"]
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    latest = await memory.message_page(p["id"], limit=50)
    older = await memory.message_page(p["id"], limit=50, before_id=latest["before_id"])
    assert len(latest["items"]) == 50 and latest["has_more"]
    assert older["items"][-1]["id"] < latest["items"][0]["id"]
