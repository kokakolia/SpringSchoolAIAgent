from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from src import memory, plans
from src.llm import ModelUnavailable, YandexClient
from src.memory_schemas import ContextOperation, CreateProject, ProjectState
from src.plan_schemas import ChangeTask, LoseTime, PlanProposal, PlanTask, ProposePlan
from src.planner import plan_diff, validate_plan
from src.server import app


def proposal():
    return PlanProposal(
        summary="Сохраним рабочий сценарий и время на проверку.",
        buffer_minutes=20,
        assumptions=["Оценки предварительные"],
        tasks=[
            PlanTask(
                id="api",
                title="Проверить API",
                estimate_minutes=40,
                priority="must",
                decision="keep",
                done_when="Сохранение работает",
                reason="Разблокирует интерфейс",
            ),
            PlanTask(
                id="ui",
                title="Собрать интерфейс",
                estimate_minutes=80,
                priority="must",
                decision="keep",
                depends_on=["api"],
                done_when="Сценарий доступен в браузере",
                reason="Нужно для показа",
                constraint_refs=[1],
            ),
            PlanTask(
                id="animation",
                title="Добавить анимации",
                estimate_minutes=60,
                priority="could",
                decision="keep",
                depends_on=["ui"],
                done_when="Переходы работают",
                reason="Сделает показ выразительнее",
            ),
        ],
    )


def state(**changes):
    return ProjectState(
        goal="Показать проект",
        success_criteria=["Рабочий сценарий"],
        constraints=["Нужен интерфейс"],
        available_minutes=240,
        deadline="2030-11-01T18:00:00+03:00",
        **changes,
    ).model_dump(mode="json")


class Client(YandexClient):
    async def count_tokens(self, messages):
        return memory.estimate_tokens(messages)

    def __init__(self, value=None):
        self.value = value or proposal()
        self.calls = 0

    async def complete(self, messages, *, json_mode=False):
        self.calls += 1
        assert json_mode
        return self.value.model_dump_json(), {"inputTextTokens": "120"}


async def project():
    return await memory.create_project(
        CreateProject(title="Защита", state=ProjectState.model_validate(state()))
    )


def operation(revision):
    return ContextOperation(request_id=uuid4(), base_revision=revision)


async def accepted():
    p = await project()
    result = await plans.propose(
        p["id"], ProposePlan(**operation(0).model_dump(), text="Составь план"), Client()
    )
    await plans.accept(p["id"], result["plan"]["id"], operation(1))
    return p["id"], result["plan"]["id"]


def test_validator_computes_capacity_dependencies_and_explanations():
    value = proposal()
    result = validate_plan(value, state(), {})
    assert result["valid"]
    assert result["required_minutes"] == 200
    assert [s["task_id"] for s in result["schedule"]] == ["api", "ui", "animation"]
    value.tasks[0].depends_on = ["ui"]
    assert "dependency_cycle" in {e["code"] for e in validate_plan(value, state(), {})["errors"]}
    value.tasks[0].depends_on = []
    value.tasks[1].decision = "defer"
    result = validate_plan(value, state(), {})
    codes = {e["code"] for e in result["errors"]}
    assert {"tradeoff_missing", "deferred_dependency", "uncovered_constraint"} <= codes


def test_validator_blocks_protected_and_silently_removed_tasks():
    old = proposal()
    new = proposal()
    new.tasks[-1].decision = "defer"
    new.tasks[-1].tradeoff = "Показ будет менее выразительным"
    result = validate_plan(new, state(), {"animation": {"pinned": True}}, old.model_dump())
    assert "protected_task" in {e["code"] for e in result["errors"]}
    new.tasks.pop()
    assert "silent_removal" in {
        e["code"] for e in validate_plan(new, state(), {}, old.model_dump())["errors"]
    }


def test_calendar_schedule_splits_work_and_respects_deadline():
    now = datetime(2030, 11, 1, 8, tzinfo=timezone.utc)
    calendar = state(
        work_windows=[
            {"start": "2030-11-01T08:00:00+00:00", "end": "2030-11-01T09:00:00+00:00"},
            {"start": "2030-11-01T10:00:00+00:00", "end": "2030-11-01T13:00:00+00:00"},
        ]
    )
    result = validate_plan(proposal(), calendar, {}, now=now)
    assert result["valid"]
    ui = next(s for s in result["schedule"] if s["task_id"] == "ui")
    assert len(ui["segments"]) == 2
    calendar["deadline"] = "2030-11-01T10:30:00+00:00"
    result = validate_plan(proposal(), calendar, {}, now=now)
    assert not result["valid"]
    assert result["capacity_minutes"] == 90


async def test_proposal_acceptance_and_replay_are_transactional():
    p = await project()
    client = Client()
    request = ProposePlan(**operation(0).model_dump(), text="Составь план")
    result = await plans.propose(p["id"], request, client)
    assert result["plan"]["validation"]["valid"]
    assert (await plans.workspace(p["id"]))["accepted"] is None
    assert (await plans.propose(p["id"], request, client))["replayed"]
    assert client.calls == 1
    action = operation(1)
    await plans.accept(p["id"], result["plan"]["id"], action)
    assert (await plans.accept(p["id"], result["plan"]["id"], action))["replayed"]
    workspace = await plans.workspace(p["id"])
    assert workspace["project"]["revision"] == 2
    assert workspace["accepted"]["id"] == result["plan"]["id"]
    assert len(workspace["events"]) == 1


async def test_time_loss_invalidates_proposal_and_requires_replan():
    pid, _ = await accepted()
    request = ProposePlan(**operation(2).model_dump(), text="Уточни план")
    changed = proposal()
    changed.tasks[0].estimate_minutes += 10
    draft = await plans.propose(pid, request, Client(changed))
    event = LoseTime(**operation(3).model_dump(), minutes=120)
    await plans.lose_time(pid, event)
    assert (await plans.lose_time(pid, event))["replayed"]
    with pytest.raises(memory.MemoryError, match="Обстоятельства изменились"):
        await plans.accept(pid, draft["plan"]["id"], operation(4))
    board = await plans.workspace(pid)
    assert board["project"]["state"]["available_minutes"] == 120
    assert board["draft_stale"]
    assert not board["current_validation"]["valid"]


async def test_explanation_only_replan_keeps_accepted_tasks_editable():
    pid, accepted_id = await accepted()
    unchanged = proposal()
    unchanged.summary = "Тот же план, с новым объяснением"
    unchanged.tasks[0].reason = "Ещё одно объяснение приоритета"
    client = Client(unchanged)
    request = ProposePlan(**operation(2).model_dump(), text="Почему именно так?")
    result = await plans.propose(pid, request, client)
    assert result["plan"]["validation"]["unchanged"]
    workspace = await plans.workspace(pid)
    assert workspace["draft"] is None
    assert workspace["accepted"]["id"] == accepted_id
    assert len(workspace["plans"]) == 2
    assert len(await memory.list_messages(pid)) == 4
    assert (await plans.propose(pid, request, client))["replayed"]
    assert client.calls == 1
    with pytest.raises(memory.MemoryError, match="План не изменился"):
        await plans.accept(pid, result["plan"]["id"], operation(3))
    await plans.change_task(pid, "api", ChangeTask(**operation(3).model_dump(), done=True))
    assert (await plans.workspace(pid))["progress"]["api"]["done"]


async def test_changed_reserve_still_requires_acceptance():
    pid, _ = await accepted()
    changed = proposal()
    changed.buffer_minutes += 5
    result = await plans.propose(
        pid, ProposePlan(**operation(2).model_dump(), text="Увеличь резерв"), Client(changed)
    )
    assert not result["plan"]["validation"]["unchanged"]
    assert (await plans.workspace(pid))["draft"]["id"] == result["plan"]["id"]
    await plans.accept(pid, result["plan"]["id"], operation(3))


async def test_invalid_plan_cannot_be_accepted_or_erase_previous():
    pid, old = await accepted()
    value = proposal()
    value.tasks[-1].estimate_minutes = 1000
    draft = await plans.propose(
        pid, ProposePlan(**operation(2).model_dump(), text="Уточни"), Client(value)
    )
    with pytest.raises(memory.MemoryError) as error:
        await plans.accept(pid, draft["plan"]["id"], operation(3))
    assert error.value.status == 422
    assert (await plans.workspace(pid))["accepted"]["id"] == old


async def test_progress_spends_actual_time_and_keeps_completed_tasks():
    pid, _ = await accepted()
    with pytest.raises(memory.MemoryError, match="Сначала завершите"):
        await plans.change_task(pid, "ui", ChangeTask(**operation(2).model_dump(), done=True))
    done = ChangeTask(**operation(2).model_dump(), done=True, actual_minutes=50)
    await plans.change_task(pid, "api", done)
    assert (await plans.change_task(pid, "api", done))["replayed"]
    board = await plans.workspace(pid)
    assert board["progress"]["api"]["done"]
    assert board["project"]["state"]["available_minutes"] == 190
    assert board["current_validation"]["work_minutes"] == 140
    await plans.change_task(pid, "api", ChangeTask(**operation(3).model_dump(), done=False))
    assert (await plans.workspace(pid))["project"]["state"]["available_minutes"] == 240


async def test_concurrent_change_discards_generated_plan():
    p = await project()

    class Concurrent(Client):
        async def complete(self, messages, *, json_mode=False):
            await plans.lose_time(p["id"], LoseTime(**operation(0).model_dump(), minutes=30))
            return await super().complete(messages, json_mode=json_mode)

    with pytest.raises(memory.MemoryError, match="Проект изменился"):
        await plans.propose(
            p["id"], ProposePlan(**operation(0).model_dump(), text="План"), Concurrent()
        )
    board = await plans.workspace(p["id"])
    assert board["plans"] == []
    assert await memory.list_messages(p["id"]) == []


async def test_malformed_output_does_not_mutate_state():
    p = await project()

    class Broken(Client):
        async def complete(self, messages, *, json_mode=False):
            return '{"tasks":"incorrect"}', {}

    with pytest.raises(ModelUnavailable):
        await plans.propose(
            p["id"], ProposePlan(**operation(0).model_dump(), text="План"), Broken()
        )
    assert (await plans.workspace(p["id"]))["project"]["revision"] == 0


async def test_plan_api_and_static_frontend(monkeypatch):
    value = proposal()

    async def complete(self, messages, *, json_mode=False):
        return value.model_dump_json(), {}

    monkeypatch.setattr(YandexClient, "complete", complete)
    monkeypatch.setattr(YandexClient, "count_tokens", Client.count_tokens)
    p = await project()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.post(
            f"/api/projects/{p['id']}/plans/propose",
            json={"request_id": str(uuid4()), "base_revision": 0, "text": "Составь план"},
        )
        assert response.status_code == 200
        plan_id = response.json()["plan"]["id"]
        response = await api.post(
            f"/api/projects/{p['id']}/plans/{plan_id}/accept",
            json={"request_id": str(uuid4()), "base_revision": 1},
        )
        assert response.status_code == 200
        assert (await api.get(f"/api/projects/{p['id']}/workspace")).json()["accepted"]
        assert (await api.get("/api/runtime")).json()["mode"] == "live"
        assert (await api.get("/")).status_code == 200
        other = await project()
        response = await api.post(
            f"/api/projects/{other['id']}/plans/{plan_id}/accept",
            json={"request_id": str(uuid4()), "base_revision": 0},
        )
        assert response.status_code == 404


def test_diff_explains_deferral():
    old = proposal().model_dump()
    new = proposal().model_dump()
    new["tasks"][-1]["decision"] = "defer"
    changes = plan_diff(old, new)
    assert changes[-1]["action"] == "deferred"
    assert changes[-1]["before"]["decision"] == "keep"


async def test_agent_repairs_plan_after_server_feedback():
    p = await project()

    class Repair(Client):
        async def complete(self, messages, *, json_mode=False):
            self.calls += 1
            value = proposal()
            if self.calls == 1:
                value.tasks[-1].estimate_minutes = 1000
            else:
                assert "over_capacity" in messages[-1]["text"]
                assert "available_minutes" in messages[-1]["text"]
            return value.model_dump_json(), {"inputTextTokens": "100"}

    result = await plans.propose(
        p["id"], ProposePlan(**operation(0).model_dump(), text="План"), Repair()
    )
    assert result["plan"]["validation"]["valid"]
    assert len(result["plan"]["validation"]["generation_checks"]) == 2
    assert len(result["usage"]["requests"]) == 2
    assert len(await memory.list_messages(p["id"])) == 2


def test_work_windows_cannot_overlap():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        state(
            work_windows=[
                {"start": "2030-11-01T08:00:00+00:00", "end": "2030-11-01T10:00:00+00:00"},
                {"start": "2030-11-01T09:00:00+00:00", "end": "2030-11-01T11:00:00+00:00"},
            ]
        )


def test_audit_times_are_utc_and_postgres_columns_accept_aware_datetimes():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from src.memory_models import PlanVersion, iso_utc

    assert iso_utc(datetime(2030, 1, 1, 12)) == "2030-01-01T12:00:00+00:00"
    ddl = str(CreateTable(PlanVersion.__table__).compile(dialect=postgresql.dialect()))
    assert "TIMESTAMP WITH TIME ZONE" in ddl
