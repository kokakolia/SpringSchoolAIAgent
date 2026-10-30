"""Separate module/config instances model workers sharing only the database."""

import asyncio
import importlib.util
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select
from starlette.responses import JSONResponse

from src import database, sessions
from src.config import Settings
from src.memory_models import AuthCredentials, AuthSession

A = "worker-a-test-password"
B = "worker-b-test-password"


def module(name):
    spec = importlib.util.spec_from_file_location(
        "test_worker_" + uuid4().hex, Path("src") / (name + ".py")
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def worker(password):
    auth = module("sessions")
    auth.settings = Settings(_env_file=None, app_password=SecretStr(password))
    access = module("access")
    access.settings = auth.settings
    for name in ("valid_session", "issue_session", "credentials_current", "session_headers"):
        setattr(access, name, getattr(auth, name))
    access.StaleCredentialsError = auth.StaleCredentialsError

    async def application(scope, receive, send):
        await JSONResponse({"ok": True})(scope, receive, send)

    return auth, access, access.AccessMiddleware(application)


async def test_mixed_workers_read_sessions_without_mutation_or_generation_ping_pong():
    old, _, _ = worker(A)
    new, _, _ = worker(B)
    await old.initialize_credentials()
    old_token = await old.issue_session()
    assert await new.rotate_credentials(1) == 2
    token = await new.issue_session()
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    engine = database._async_engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(4):
            await asyncio.gather(old.initialize_credentials(), new.initialize_credentials())
            assert await asyncio.gather(
                old.valid_session(None), old.valid_session(token), new.valid_session(token)
            ) == [False, True, True]
        assert not await old.valid_session(old_token)
        assert all(command == "SELECT" for command in statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    with pytest.raises(old.StaleCredentialsError):
        await old.issue_session()
    with pytest.raises(old.StaleCredentialsError):
        await old.rotate_credentials(1)
    assert await new.valid_session(token)
    async with database._get_session_maker()() as db:
        assert (await db.get(AuthCredentials, 1)).generation == 2
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 1


@pytest.mark.parametrize("outdated_password", [A, ""])
async def test_stale_http_worker_keeps_active_cookie_but_cannot_login_or_bypass_access(
    outdated_password,
):
    original, _, _ = worker(A)
    new, _, new_app = worker(B)
    await original.initialize_credentials()
    await new.rotate_credentials(1)
    stale, stale_access, stale_app = worker(outdated_password)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=new_app), base_url="https://localhost"
    ) as owner:
        assert (await owner.get("/api/projects", auth=("owner", B))).status_code == 200
        cookie = owner.cookies.get("__Host-deadline_session")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=stale_app), base_url="https://localhost"
        ) as old_api:
            assert (
                await old_api.get("/api/projects", auth=("owner", outdated_password))
            ).status_code == 503
            assert (await old_api.get("/api/projects", auth=("owner", B))).status_code == 503
            old_api.cookies.set("__Host-deadline_session", cookie)
            for _ in range(4):
                assert (await old_api.get("/api/projects")).status_code == 200
        assert (await owner.get("/api/projects")).status_code == 200
    assert stale_access.auth_throttle.global_count == 0
    async with database._get_session_maker()() as db:
        assert (await db.get(AuthCredentials, 1)).generation == 2


async def test_concurrent_explicit_rotations_only_one_can_replace_expected_generation():
    first, _, _ = worker(A)
    second, _, _ = worker(B)
    await first.initialize_credentials()
    results = await asyncio.gather(
        first.rotate_credentials(1), second.rotate_credentials(1), return_exceptions=True
    )
    assert sum(result == 2 for result in results) == 1
    assert sum(isinstance(result, Exception) for result in results) == 1
    async with database._get_session_maker()() as db:
        assert (await db.get(AuthCredentials, 1)).generation == 2


async def test_auth_admin_reports_only_version_and_rejects_stale_cas(monkeypatch):
    from src.auth_admin import auth_command
    from src.config import settings

    monkeypatch.setattr(settings, "app_password", SecretStr(A))
    await sessions.initialize_credentials()
    assert await auth_command("auth-status") == "Текущая версия: 1"
    monkeypatch.setattr(settings, "app_password", SecretStr(B))
    assert await auth_command("auth-update", 1) == "Учётные данные обновлены. Текущая версия: 2"
    with pytest.raises(sessions.StaleCredentialsError):
        await auth_command("auth-update", 1)
    assert await auth_command("auth-status") == "Текущая версия: 2"
