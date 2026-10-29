"""Use the application's URL, including asyncpg and aiosqlite, without logging secrets."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src import memory_models  # noqa: F401
from src.config import settings
from src.database import Base


def include_object(obj, name, type_, reflected, compare_to):
    return type_ != "table" or name in Base.metadata.tables


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("These migrations inspect existing tables; use an online connection")
elif context.config.attributes.get("connection") is not None:
    run(context.config.attributes["connection"])
else:
    asyncio.run(run_async())
