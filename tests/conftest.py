import os
import tempfile

import pytest

from src import database
from src.config import settings


@pytest.fixture(autouse=True)
async def db_session(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp.name}")
    database._async_engine = None
    database._async_session_maker = None
    await database.init_db()
    yield
    if database._async_engine:
        await database._async_engine.dispose()
    database._async_engine = None
    database._async_session_maker = None
    try:
        os.unlink(tmp.name)
    except PermissionError:
        pass
