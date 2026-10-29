"""Transactional startup migrations, also callable through the Alembic CLI."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text


def upgrade(connection):
    from src.database import Base

    if connection.dialect.name == "postgresql":
        # Serialize startup across workers; released at the end of this transaction.
        connection.execute(text("SELECT pg_advisory_xact_lock(742109630)"))
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "include_object": lambda obj, name, type_, reflected, compare_to: (
                type_ != "table" or name in Base.metadata.tables
            ),
        },
    )
    differences = compare_metadata(context, Base.metadata)
    inspector = inspect(connection)
    # Alembic autogenerate does not reliably detect primary key changes.
    for table in Base.metadata.sorted_tables:
        actual = inspector.get_pk_constraint(table.name)["constrained_columns"]
        if set(actual) != set(table.primary_key.columns.keys()):
            differences.append(("primary_key", table.name))
    if differences:
        raise RuntimeError("Схема базы отличается от миграций. Выполните alembic check")
