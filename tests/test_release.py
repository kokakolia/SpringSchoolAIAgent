import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src import database
from src.config import Settings
from src.legacy import export_tasks


async def test_legacy_export_keeps_all_rows_as_literal_json_without_changing_database(tmp_path):
    async with database._get_session_maker()() as session:
        session.add(database.Task(user_id=7, title="=1+1", quadrant=2, reason="Keep"))
        await session.commit()
    output = tmp_path / "historical.json"
    await export_tasks(output)
    assert json.loads(output.read_text(encoding="utf-8"))[0]["title"] == "=1+1"
    assert json.loads(output.read_text(encoding="utf-8"))[0]["user_id"] == 7
    await database.init_db()
    async with database._get_session_maker()() as session:
        assert (await session.scalar(select(database.Task))).title == "=1+1"


def test_cli_defaults_to_normal_server_and_reload_is_explicit(monkeypatch):
    import uvicorn

    from main import main

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    main(["serve", "--port", "8123"])
    assert calls[-1][1]["port"] == 8123 and calls[-1][1]["reload"] is False
    main(["serve", "--reload"])
    assert calls[-1][1]["reload"] is True


def test_config_rejects_an_impossible_context_budget():
    with pytest.raises(ValidationError, match="Резерв ответа"):
        Settings(_env_file=None, context_budget_tokens=1000, response_reserve_tokens=1000)
