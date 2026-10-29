from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import settings


class Base(DeclarativeBase):
    pass


_async_engine = None
_async_session_maker = None


class Task(Base):
    """Historical table retained for migrations and read-only export."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    quadrant: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (CheckConstraint("quadrant BETWEEN 1 AND 4"),)


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _async_engine, _async_session_maker
    if _async_session_maker is None:
        if _async_engine is None:
            _async_engine = create_async_engine(settings.database_url)
        _async_session_maker = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _async_session_maker


async def init_db():
    from src import memory_models  # noqa: F401
    from src.migrations import upgrade

    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(settings.database_url)
    async with _async_engine.begin() as conn:
        await conn.run_sync(upgrade)


async def close_db():
    global _async_engine, _async_session_maker
    if _async_engine is not None:
        await _async_engine.dispose()
    _async_engine = None
    _async_session_maker = None
