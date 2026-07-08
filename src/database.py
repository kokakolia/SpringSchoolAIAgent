from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config import settings

Base = declarative_base()
_engine = None
_SessionLocal = None


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, default=1)
    title = Column(String(500), nullable=False)
    quadrant = Column(Integer, nullable=False)
    reason = Column(String(1000), default="")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (CheckConstraint("quadrant BETWEEN 1 AND 4"),)


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url)
    return _engine


def _get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine())
    return _SessionLocal()


def init_db():
    Base.metadata.create_all(_get_engine())


def save_tasks(tasks: list[dict], user_id: int = 1) -> str:
    session = _get_session()
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
        session.commit()
        return f"Успешно: {len(tasks)} задач сохранено."
    except Exception as e:
        session.rollback()
        return f"Ошибка: {str(e)}"
    finally:
        session.close()


def read_matrix(user_id: int = 1) -> dict[int, list[dict]]:
    session = _get_session()
    try:
        rows = session.query(Task).filter(Task.user_id == user_id).order_by(Task.id).all()
    finally:
        session.close()

    by_quadrant: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
    for r in rows:
        by_quadrant.setdefault(r.quadrant, []).append(
            {
                "id": r.id,
                "title": r.title,
                "quadrant": r.quadrant,
                "reason": r.reason or "",
                "date": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            }
        )
    return by_quadrant


def delete_task_db(task_id: int, user_id: int = 1) -> str:
    session = _get_session()
    try:
        task = session.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
        if not task:
            return f"Задача #{task_id} не найдена."
        session.delete(task)
        session.commit()
        return f"Задача #{task_id} удалена."
    except Exception as e:
        session.rollback()
        return f"Ошибка: {str(e)}"
    finally:
        session.close()


def move_task_db(task_id: int, new_quadrant: int, user_id: int = 1) -> str:
    if new_quadrant not in range(1, 5):
        return "Квадрант должен быть 1-4."
    session = _get_session()
    try:
        task = session.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
        if not task:
            return f"Задача #{task_id} не найдена."
        task.quadrant = new_quadrant
        session.commit()
        return f"Задача #{task_id} перемещена в квадрант {new_quadrant}."
    except Exception as e:
        session.rollback()
        return f"Ошибка: {str(e)}"
    finally:
        session.close()
