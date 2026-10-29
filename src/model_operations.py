"""Database leases prevent concurrent model work across server processes."""

import asyncio
from datetime import timedelta
from functools import wraps

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src import database
from src.config import settings
from src.llm import ModelUnavailable
from src.logger import logger
from src.memory_models import ModelOperation, new_id, utcnow


async def claim(project_id):
    from src.memory import MemoryError, require_project

    owner = new_id()
    now = utcnow()
    async with database._get_session_maker()() as session:
        await require_project(session, project_id)
        await session.execute(
            delete(ModelOperation).where(
                ModelOperation.project_id == project_id, ModelOperation.expires_at <= now
            )
        )
        session.add(
            ModelOperation(
                project_id=project_id,
                owner=owner,
                expires_at=now + timedelta(seconds=settings.model_operation_timeout_seconds + 60),
            )
        )
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise MemoryError(
                "Агент уже работает над этим проектом. Дождитесь результата", code="operation_busy"
            ) from error
    return owner


async def release(project_id, owner):
    try:
        async with database._get_session_maker()() as session:
            await session.execute(
                delete(ModelOperation).where(
                    ModelOperation.project_id == project_id, ModelOperation.owner == owner
                )
            )
            await session.commit()
    except SQLAlchemyError:
        # A failed release expires automatically; never delete another worker's lease.
        logger.warning("Model operation lease will expire after a database release failure")


def serialized_model_operation(function):
    @wraps(function)
    async def wrapped(project_id, *args, **kwargs):
        owner = await claim(project_id)
        try:
            async with asyncio.timeout(settings.model_operation_timeout_seconds):
                return await function(project_id, *args, **kwargs)
        except TimeoutError as error:
            raise ModelUnavailable(
                "Агент не успел завершить запрос. Проверьте состояние проекта"
            ) from error
        finally:
            await asyncio.shield(release(project_id, owner))

    return wrapped
