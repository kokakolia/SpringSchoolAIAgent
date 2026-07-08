import pytest

from src import database
from src.config import settings


@pytest.fixture(autouse=True)
def db_session(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:///:memory:")
    database._engine = None
    database._SessionLocal = None
    database.init_db()
    yield
    database._engine = None
    database._SessionLocal = None
