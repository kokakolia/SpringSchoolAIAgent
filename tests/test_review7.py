import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from src import access, database, memory, plans, sessions
from src.config import settings
from src.memory_models import AuthCredentials, AuthSession, ProjectEvent, TaskProgress
from src.memory_schemas import SendMessage
from src.plan_schemas import ChangeTask
from src.server import app
from tests.test_memory import FakeClient
from tests.test_plans import accepted, operation

PASSWORD = "review-seven-test-password"


def login_scope():
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"app.example")],
        "server": ("app.example", 443),
        "client": ("203.0.113.10", 123),
    }


@pytest.mark.parametrize("trickle", [False, True])
async def test_login_body_deadline_returns_408_without_checking_password(monkeypatch, trickle):
    monkeypatch.setattr(settings, "app_password", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "auth_login_body_timeout_seconds", 0.03)
    calls = []
    monkeypatch.setattr(access, "verify_password", lambda *args: calls.append(args))
    received = False

    async def receive():
        nonlocal received
        if trickle:
            await asyncio.sleep(0.005)
            return {"type": "http.request", "body": b"x", "more_body": True}
        if not received:
            received = True
            return {"type": "http.request", "body": b"username=owner&password=", "more_body": True}
        await asyncio.Event().wait()

    output = []

    async def send(message):
        output.append(message)

    await asyncio.wait_for(app(login_scope(), receive, send), 2)
    assert output[0]["status"] == 408
    assert not calls and not access.auth_throttle.pending


async def test_login_slots_release_on_cancellation_and_existing_session_is_available(monkeypatch):
    monkeypatch.setattr(settings, "app_password", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "auth_max_concurrent_logins", 1)
    token = await sessions.issue_session()
    entered = asyncio.Event()

    async def receive():
        entered.set()
        await asyncio.Event().wait()

    async def send(message):
        pass

    task = asyncio.create_task(app(login_scope(), receive, send))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.11", 123)),
            base_url="https://app.example",
        ) as api:
            assert (await api.get("/api/projects", auth=("owner", PASSWORD))).status_code == 503
            api.cookies.set("__Host-deadline_session", token)
            assert (await api.get("/api/projects")).status_code == 200
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not access.auth_throttle.pending
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.12", 123)),
        base_url="https://app.example",
    ) as api:
        assert (await api.get("/api/projects", auth=("owner", PASSWORD))).status_code == 200
    assert not access.auth_throttle.pending


async def test_successful_basic_logins_do_not_consume_failure_budget(monkeypatch):
    monkeypatch.setattr(settings, "app_password", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "auth_global_attempts_per_minute", 3)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.2", 123))
    for _ in range(5):
        async with httpx.AsyncClient(transport=transport, base_url="https://app.example") as api:
            assert (await api.get("/api/projects", auth=("owner", PASSWORD))).status_code == 200
    assert access.auth_throttle.global_count == 0
    async with httpx.AsyncClient(transport=transport, base_url="https://app.example") as api:
        assert (await api.get("/api/projects", auth=("owner", "invalid"))).status_code == 401
    for _ in range(2):
        async with httpx.AsyncClient(transport=transport, base_url="https://app.example") as api:
            assert (await api.get("/api/projects", auth=("owner", PASSWORD))).status_code == 200
    assert access.auth_throttle.global_count == 1


async def test_credential_generation_persists_across_password_rollback_and_engine_restart(
    monkeypatch,
):
    monkeypatch.setattr(settings, "app_password", SecretStr(PASSWORD))
    old = await sessions.issue_session()
    monkeypatch.setattr(settings, "app_password", SecretStr("different-review-seven-password"))
    await sessions.rotate_credentials(1)
    middle = await sessions.issue_session()
    await database.close_db()
    await database.init_db()
    monkeypatch.setattr(settings, "app_password", SecretStr(PASSWORD))
    await sessions.rotate_credentials(2)
    assert not await sessions.valid_session(old)
    assert not await sessions.valid_session(middle)
    fresh = await sessions.issue_session()
    assert await sessions.valid_session(fresh)
    async with database._get_session_maker()() as session:
        assert (await session.get(AuthCredentials, 1)).generation == 3
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_concurrent_first_logins_share_a_credential_generation(monkeypatch):
    monkeypatch.setattr(settings, "app_password", SecretStr(PASSWORD))
    tokens = await asyncio.gather(*(sessions.issue_session() for _ in range(3)))
    assert all(await asyncio.gather(*(sessions.valid_session(t) for t in tokens)))
    async with database._get_session_maker()() as session:
        assert (await session.get(AuthCredentials, 1)).generation == 1


async def test_correcting_actual_time_changes_only_the_difference_and_replay_is_safe():
    pid, _ = await accepted()
    await plans.change_task(
        pid, "api", ChangeTask(**operation(2).model_dump(), done=True, actual_minutes=40)
    )
    body = ChangeTask(**operation(3).model_dump(), done=True, actual_minutes=100)
    await plans.change_task(pid, "api", body)
    replay = await plans.change_task(pid, "api", body)
    assert replay["replayed"]
    board = await plans.workspace(pid)
    assert board["project"]["state"]["available_minutes"] == 140
    assert board["progress"]["api"]["spent_minutes"] == 100
    async with database._get_session_maker()() as session:
        row = await session.get(TaskProgress, (pid, "api"))
        event = await session.scalar(
            select(ProjectEvent).where(
                ProjectEvent.project_id == pid, ProjectEvent.request_id == str(body.request_id)
            )
        )
        assert row.debited_minutes == event.payload["actual_minutes"] == 100
    await plans.change_task(
        pid, "api", ChangeTask(**operation(4).model_dump(), done=True, actual_minutes=20)
    )
    assert (await memory.get_project(pid))["state"]["available_minutes"] == 220
    await plans.change_task(pid, "api", ChangeTask(**operation(5).model_dump(), done=False))
    assert (await memory.get_project(pid))["state"]["available_minutes"] == 240


@pytest.mark.parametrize("compress", [False, True])
async def test_dialogue_contains_current_plan_progress_and_events_even_after_compression(
    monkeypatch, compress
):
    pid, _ = await accepted()
    await plans.change_task(
        pid, "api", ChangeTask(**operation(2).model_dump(), done=True, actual_minutes=40)
    )

    class Client(FakeClient):
        async def count_tokens(self, messages):
            if compress and self.summary_calls == 0:
                return 12001
            return 100

    client = Client()
    await memory.send_message(
        pid,
        SendMessage(request_id=uuid4(), base_revision=3, text="Какая задача следующая?"),
        client,
    )
    messages = client.calls[-1]
    assert messages[0]["role"] == "system"
    evidence = json.loads(
        messages[0]["text"].split("приоритетнее старой переписки и сводки:\n", 1)[1]
    )
    assert evidence["as_of_revision"] == 3
    assert evidence["accepted_plan"]["tasks"][0]["title"] == "Проверить API"
    assert evidence["progress"]["api"]["done"] is True
    assert evidence["progress"]["api"]["spent_minutes"] == 40
    assert any(e["kind"] == "task_changed" for e in evidence["events"])
    assert messages[-1]["text"] == "Какая задача следующая?"
    assert client.summary_calls == int(compress)
