from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from src.config import settings

Base = declarative_base()
_async_engine = None
_async_session_maker = None


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, default=1)
    title = Column(String(500), nullable=False)
    quadrant = Column(Integer, nullable=False)
    reason = Column(String(1000), default="")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (CheckConstraint("quadrant BETWEEN 1 AND 4"),)


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _async_engine, _async_session_maker
    if _async_session_maker is None:
        _async_engine = create_async_engine(settings.database_url)
        _async_session_maker = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _async_session_maker


async def init_db():
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(settings.database_url)
    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def save_tasks(tasks: list[dict], user_id: int = 1) -> str:
    maker = _get_session_maker()
    async with maker() as session:
        try:
            now = datetime.now()
            for task in tasks:
                t = Task(
                    user_id=user_id,
                    title=task.get("title", "Без названия"),
                    quadrant=task.get("quadrant", 0),
                    reason=task.get("reason", ""),
                    created_at=now,
                )
                session.add(t)
            await session.commit()
            return f"Успешно: {len(tasks)} задач сохранено."
        except Exception as e:
            await session.rollback()
            return f"Ошибка: {str(e)}"


async def read_matrix(user_id: int = 1) -> dict[int, list[dict]]:
    maker = _get_session_maker()
    async with maker() as session:
        result = await session.execute(
            select(Task).where(Task.user_id == user_id).order_by(Task.id)
        )
        rows = result.scalars().all()

    by_quadrant: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
    for r in rows:
        by_quadrant.setdefault(r.quadrant or 0, []).append(
            {
                "id": r.id,
                "title": r.title,
                "quadrant": r.quadrant,
                "reason": r.reason or "",
                "date": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            }
        )
    return by_quadrant


async def delete_task_db(task_id: int, user_id: int = 1) -> str:
    maker = _get_session_maker()
    async with maker() as session:
        try:
            result = await session.execute(
                select(Task).where(Task.id == task_id, Task.user_id == user_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                return f"Задача #{task_id} не найдена."
            await session.delete(task)
            await session.commit()
            return f"Задача #{task_id} удалена."
        except Exception as e:
            await session.rollback()
            return f"Ошибка: {str(e)}"


async def move_task_db(task_id: int, new_quadrant: int, user_id: int = 1) -> str:
    if new_quadrant not in range(1, 5):
        return "Квадрант должен быть 1-4."
    maker = _get_session_maker()
    async with maker() as session:
        try:
            result = await session.execute(
                select(Task).where(Task.id == task_id, Task.user_id == user_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                return f"Задача #{task_id} не найдена."
            task.quadrant = new_quadrant
            await session.commit()
            return f"Задача #{task_id} перемещена в квадрант {new_quadrant}."
        except Exception as e:
            await session.rollback()
            return f"Ошибка: {str(e)}"
