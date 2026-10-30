import os
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src import database
from src.config import settings


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    from src.access import auth_throttle

    auth_throttle.clear()
    monkeypatch.setattr(settings, "app_password", SecretStr(""))
    monkeypatch.setattr(settings, "app_username", "owner")
    monkeypatch.setattr(settings, "app_allow_http", False)
    original_client = httpx.AsyncClient

    def client(*args, **kwargs):
        if kwargs.get("transport") is None:
            raise AssertionError("Tests must supply a mock transport; external HTTP is disabled")
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


@pytest.fixture(autouse=True)
async def db_session(monkeypatch, tmp_path):
    test_url = os.getenv("TEST_DATABASE_URL")
    schema = "test_" + uuid4().hex
    admin = None
    if test_url:
        if not test_url.startswith("postgresql+asyncpg://"):
            raise ValueError("TEST_DATABASE_URL must use postgresql+asyncpg")
        admin = create_async_engine(test_url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        monkeypatch.setattr(settings, "database_url", test_url)
        original_create_engine = database.create_async_engine
        monkeypatch.setattr(
            database,
            "create_async_engine",
            lambda url: original_create_engine(
                url, connect_args={"server_settings": {"search_path": schema}}
            ),
        )
    else:
        path = tmp_path / "test.db"
        monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    database._async_engine = None
    database._async_session_maker = None
    try:
        await database.init_db()
        yield
    finally:
        if database._async_engine:
            await database._async_engine.dispose()
        database._async_engine = None
        database._async_session_maker = None
        if admin is not None:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
