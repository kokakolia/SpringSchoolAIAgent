"""Migration checks use disposable databases; never send requests to AI services."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src import database
from src.migrations import upgrade


def config(connection):
    result = Config("alembic.ini")
    result.attributes["connection"] = connection
    return result


def test_fresh_schema_round_trip_and_existing_data_adoption():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        upgrade(connection)
        command.check(config(connection))
        connection.execute(
            text("INSERT INTO tasks(id,user_id,title,quadrant) VALUES (1,1,'Keep this task',2)")
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,title,state,revision,created_at) "
                "VALUES ('project','Keep this project','{}',7,'2026-10-29 10:00:00')"
            )
        )
        connection.execute(text("DROP TABLE alembic_version"))
        upgrade(connection)
        upgrade(connection)
        assert connection.scalar(text("SELECT title FROM tasks WHERE id=1")) == "Keep this task"
        assert connection.scalar(text("SELECT revision FROM projects WHERE id='project'")) == 7
        command.downgrade(config(connection), "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        upgrade(connection)
        command.check(config(connection))
    engine.dispose()


async def test_application_migration_is_repeatable_and_preserves_project():
    from src.memory import create_project, get_project
    from src.memory_schemas import CreateProject, ProjectState

    project = await create_project(CreateProject(title="Keep", state=ProjectState(goal="Ship")))
    await database.init_db()
    await database._async_engine.dispose()
    database._async_engine = None
    database._async_session_maker = None
    await database.init_db()
    assert await get_project(project["id"]) == project


async def test_adopt_unversioned_schema_and_repair_postgres_utc_timestamps():
    from src.memory import create_project, get_project
    from src.memory_schemas import CreateProject, ProjectState

    project = await create_project(CreateProject(title="Keep", state=ProjectState(goal="Ship")))
    async with database._async_engine.begin() as connection:
        before = await connection.scalar(text("SELECT created_at FROM projects"))
        await connection.execute(text("DROP TABLE alembic_version"))
        if connection.dialect.name == "postgresql":
            for table in (
                "projects",
                "project_messages",
                "context_versions",
                "plan_versions",
                "project_events",
            ):
                await connection.execute(
                    text(
                        f'ALTER TABLE "{table}" ALTER COLUMN created_at '
                        "TYPE TIMESTAMP WITHOUT TIME ZONE USING created_at AT TIME ZONE 'UTC'"
                    )
                )
        await connection.run_sync(upgrade)
        after = await connection.scalar(text("SELECT created_at FROM projects"))
        assert before == after
    assert await get_project(project["id"]) == project


def test_incompatible_existing_schema_is_reported():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        upgrade(connection)
        connection.execute(text("ALTER TABLE projects ADD COLUMN unexpected INTEGER"))
        with pytest.raises(RuntimeError, match="Схема базы отличается"):
            upgrade(connection)
    engine.dispose()


def test_budget_debit_migration_keeps_existing_completed_progress():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        command.upgrade(config(connection), "0002_utc_timestamps")
        connection.execute(
            text(
                "INSERT INTO projects(id,title,state,revision,created_at) "
                "VALUES ('p','Old','{}',1,'2026-10-29')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_task_progress(project_id,task_id,done,pinned,spent_minutes) "
                "VALUES ('p','task',1,0,50)"
            )
        )
        upgrade(connection)
        assert connection.execute(
            text("SELECT spent_minutes,debited_minutes FROM project_task_progress")
        ).one() == (50, 50)
    engine.dispose()


def test_time_ledger_migration_preserves_legacy_balance_and_known_losses():
    import json

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        command.upgrade(config(connection), "0004_model_operations")
        connection.execute(
            text(
                "INSERT INTO projects(id,title,state,revision,created_at) "
                "VALUES ('p','Old',:state,3,'2026-10-29')"
            ),
            {"state": json.dumps({"available_minutes": 0})},
        )
        connection.execute(
            text(
                "INSERT INTO project_task_progress"
                "(project_id,task_id,done,pinned,spent_minutes,debited_minutes) "
                "VALUES ('p','old',1,0,40,40)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_events"
                "(id,project_id,request_id,kind,payload,revision,created_at) "
                "VALUES ('e','p','r','time_lost',:payload,3,'2026-10-29')"
            ),
            {"payload": json.dumps({"minutes": 220})},
        )
        upgrade(connection)
        assert connection.execute(
            text("SELECT time_budget_minutes,time_lost_minutes,time_spent_minutes FROM projects")
        ).one() == (260, 220, 40)
        assert (
            json.loads(connection.scalar(text("SELECT state FROM projects")))["available_minutes"]
            == 0
        )
        command.check(config(connection))
    engine.dispose()
