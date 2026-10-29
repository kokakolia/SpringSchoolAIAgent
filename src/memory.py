"""Project dialogue and context snapshots with optimistic concurrency control.

Network calls happen outside database transactions. A revision comparison protects
the final commit from overwriting user edits or another model response.
"""

import json
import math

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import database
from src.config import settings
from src.llm import YandexClient
from src.memory_models import (
    ContextVersion,
    Message,
    ModelOperation,
    PlanVersion,
    Project,
    ProjectEvent,
    TaskProgress,
    iso_utc,
    new_id,
)
from src.memory_schemas import CreateProject, MemorySummary, SendMessage, UpdateProject
from src.model_operations import serialized_model_operation

AGENT_PROMPT = """Ты — агент «До дедлайна». Помоги довести проект до проверяемого результата.
Уточняй цель, критерии успеха, дедлайн и доступное рабочее время. При недостатке
данных задай короткие вопросы. Предлагай приоритеты и компромиссы, объясняй пользу
и потери. Учитывай возражения и ручные правки пользователя. Отделяй факты от оценок.
Не объявляй неподтверждённый план выполнимым. Не утверждай, что изменил задачи,
требования или принятый план: сейчас ты обсуждаешь предложение с пользователем.
Сводка диалога — вспомогательная память, которая может содержать ошибки.
Подтверждённое состояние проекта ниже имеет приоритет над сводкой.
"""


class MemoryError(Exception):
    def __init__(self, detail: str, status: int = 409, code: str | None = None):
        self.detail = detail
        self.status = status
        self.code = code


def estimate_tokens(messages: list[dict]) -> int:
    """Heuristic, NOT a provider token count or guaranteed upper bound."""
    return math.ceil(len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) / 3)


def metrics(messages: list[dict], exact_tokens: int | None = None) -> dict:
    count = estimate_tokens(messages) if exact_tokens is None else exact_tokens
    total = count + settings.response_reserve_tokens
    return {
        "input_tokens": count,
        "count_method": "estimated_utf8" if exact_tokens is None else "yandex_tokenizer",
        "response_reserve_tokens": settings.response_reserve_tokens,
        "configured_budget_tokens": settings.context_budget_tokens,
        "budget_fraction": round(total / settings.context_budget_tokens, 4),
        "compression_recommended": total
        >= settings.context_budget_tokens * settings.context_compression_threshold,
    }


def project_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "title": project.title,
        "state": project.state,
        "revision": project.revision,
        "current_context_id": project.current_context_id,
    }


def version_dict(version: ContextVersion, *, include_messages=False) -> dict:
    result: dict = {
        "id": version.id,
        "parent_id": version.parent_id,
        "kind": version.kind,
        "summary": version.summary,
        "covered_message_id": version.covered_message_id,
        "estimated_tokens": version.estimated_tokens,
        "count_method": "estimated_utf8",
        "created_at": iso_utc(version.created_at),
    }
    if include_messages:
        result["messages"] = version.messages
    return result


async def require_project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise MemoryError("Проект не найден", 404)
    return project


async def require_version(session, project_id, version_id) -> ContextVersion:
    version = await session.get(ContextVersion, version_id)
    if version is None or version.project_id != project_id:
        raise MemoryError("Версия контекста не найдена в этом проекте", 404)
    return version


async def advance(session, project_id, revision, **values):
    # Acquire the revision before flushing staged snapshots from a concurrent request.
    with session.no_autoflush:
        result = await session.execute(
            update(Project)
            .where(Project.id == project_id, Project.revision == revision)
            .values(revision=revision + 1, **values)
        )
    if result.rowcount != 1:
        raise MemoryError("Проект изменился. Загрузите актуальную версию и повторите запрос")


def check_revision(project: Project, expected: int):
    if project.revision != expected:
        raise MemoryError("Проект изменился. Загрузите актуальную версию и повторите запрос")


def assemble(state: dict, summary: dict | None, dialogue: list[dict]) -> list[dict]:
    messages = [
        {
            "role": "system",
            "text": AGENT_PROMPT
            + "\nПодтверждённое состояние:\n"
            + json.dumps(state, ensure_ascii=False),
        }
    ]
    if summary:
        messages.append(
            {
                "role": "user",
                "text": "Краткая память предыдущего диалога:\n"
                + json.dumps(summary, ensure_ascii=False),
            }
        )
    return messages + dialogue


async def load_context(session, project: Project, version: ContextVersion | None = None):
    if version is None and project.current_context_id:
        version = await require_version(session, project.id, project.current_context_id)
    cutoff = version.covered_message_id if version else 0
    rows = (
        await session.scalars(
            select(Message)
            .where(Message.project_id == project.id, Message.id > cutoff)
            .order_by(Message.id)
        )
    ).all()
    dialogue = [{"role": row.role, "text": row.text} for row in rows]
    summary = version.summary if version else None
    last_id = rows[-1].id if rows else cutoff
    return assemble(project.state, summary, dialogue), last_id, summary, cutoff


async def create_project(body: CreateProject):
    async with database._get_session_maker()() as session:
        project = Project(title=body.title, state=body.state.model_dump(mode="json"))
        session.add(project)
        await session.commit()
        return project_dict(project)


async def list_projects():
    async with database._get_session_maker()() as session:
        projects = (await session.scalars(select(Project).order_by(Project.created_at))).all()
        return [project_dict(p) for p in projects]


async def get_project(project_id: str):
    async with database._get_session_maker()() as session:
        return project_dict(await require_project(session, project_id))


async def update_project(project_id: str, body: UpdateProject):
    async with database._get_session_maker()() as session:
        project = await require_project(session, project_id)
        check_revision(project, body.base_revision)
        state = body.state.model_dump(mode="json") if body.state is not None else project.state
        title = body.title if body.title is not None else project.title
        state_changed = state != project.state
        if not state_changed and title == project.title:
            return project_dict(project)
        old_title = project.title
        await advance(
            session,
            project_id,
            body.base_revision,
            state=state,
            title=title,
        )
        session.add(
            ProjectEvent(
                project_id=project_id,
                request_id=new_id(),
                kind="state_updated" if state_changed else "project_renamed",
                revision=body.base_revision + 1,
                payload={"state": state, "title": title}
                if state_changed
                else {"before": old_title, "title": title},
            )
        )
        await session.commit()
    return await get_project(project_id)


async def delete_project(project_id: str, revision: int):
    async with database._get_session_maker()() as session:
        project = await require_project(session, project_id)
        check_revision(project, revision)
        # Lock this revision before removing children. Concurrent model replies fail CAS.
        await advance(session, project_id, revision)
        for model in (
            ModelOperation,
            Message,
            ContextVersion,
            PlanVersion,
            TaskProgress,
            ProjectEvent,
        ):
            await session.execute(delete(model).where(model.project_id == project_id))
        await session.execute(delete(Project).where(Project.id == project_id))
        await session.commit()
    return {"id": project_id, "deleted": True}


async def get_context(project_id: str, client: YandexClient | None = None):
    async with database._get_session_maker()() as session:
        project = await require_project(session, project_id)
        messages, last_id, _, _ = await load_context(session, project)
        revision, current_id = project.revision, project.current_context_id
    exact = await client.count_tokens(messages) if client else None
    return {
        "revision": revision,
        "current_context_id": current_id,
        "last_message_id": last_id,
        "messages": messages,
        "usage": metrics(messages, exact),
    }


async def list_messages(project_id: str):
    async with database._get_session_maker()() as session:
        await require_project(session, project_id)
        rows = (
            await session.scalars(
                select(Message).where(Message.project_id == project_id).order_by(Message.id)
            )
        ).all()
        return [
            {
                "id": row.id,
                "role": row.role,
                "text": row.text,
                "request_id": row.request_id,
                "usage": row.usage,
            }
            for row in rows
        ]


@serialized_model_operation
async def send_message(project_id: str, body: SendMessage, client: YandexClient):
    from src.context_budget import ContextGuard

    if body.text == "/compress":
        return await _compress_context(project_id, str(body.request_id), body.base_revision, client)
    async with database._get_session_maker()() as session:
        project = await require_project(session, project_id)
        previous = (
            await session.scalars(
                select(Message)
                .where(Message.project_id == project_id, Message.request_id == str(body.request_id))
                .order_by(Message.id)
            )
        ).all()
        if previous:
            if previous[0].text != body.text:
                raise MemoryError("request_id уже использован для другого сообщения")
            return {"response": previous[-1].text, "revision": project.revision, "replayed": True}
        check_revision(project, body.base_revision)
        guard = await ContextGuard.load(session, project, str(body.request_id), client)
        messages = guard.context + [{"role": "user", "text": body.text}]
    messages = await guard.prepare(messages)
    text, usage = await client.complete(messages)
    usage = {**usage, "context_checks": guard.checks}
    async with database._get_session_maker()() as session:
        await advance(session, project_id, body.base_revision, **guard.persist(session))
        session.add_all(
            [
                Message(
                    project_id=project_id,
                    request_id=str(body.request_id),
                    role="user",
                    text=body.text,
                ),
                Message(
                    project_id=project_id,
                    request_id=str(body.request_id),
                    role="assistant",
                    text=text,
                    usage=usage,
                ),
            ]
        )
        await session.commit()
    return {"response": text, "revision": body.base_revision + 1, "usage": usage, "replayed": False}


async def _compress_context(project_id: str, request_id: str, revision: int, client: YandexClient):
    async with database._get_session_maker()() as session:
        project = await require_project(session, project_id)
        existing = await session.scalar(
            select(ContextVersion).where(
                ContextVersion.project_id == project_id,
                ContextVersion.request_id == request_id,
                ContextVersion.kind == "compressed",
            )
        )
        if existing:
            return {
                "context": version_dict(existing),
                "revision": project.revision,
                "replayed": True,
            }
        check_revision(project, revision)
        messages, last_id, old_summary, old_cutoff = await load_context(session, project)
        state, parent = project.state, project.current_context_id
    if not last_id:
        raise MemoryError("Нет сообщений для сжатия", 422)
    summary = MemorySummary.model_validate(await client.summarize(messages)).model_dump()
    compact = assemble(state, summary, [])
    if estimate_tokens(compact) >= estimate_tokens(messages):
        raise MemoryError("Сжатие не уменьшило контекст. Текущая версия сохранена", 422)
    before_id, after_id = new_id(), new_id()
    async with database._get_session_maker()() as session:
        await advance(session, project_id, revision, current_context_id=after_id)
        before = ContextVersion(
            id=before_id,
            project_id=project_id,
            request_id=request_id,
            parent_id=parent,
            kind="before_compression",
            messages=messages,
            summary=old_summary,
            covered_message_id=old_cutoff,
            estimated_tokens=estimate_tokens(messages),
        )
        after = ContextVersion(
            id=after_id,
            project_id=project_id,
            request_id=request_id,
            parent_id=before_id,
            kind="compressed",
            messages=compact,
            summary=summary,
            covered_message_id=last_id,
            estimated_tokens=estimate_tokens(compact),
        )
        session.add_all([before, after])
        await session.commit()
        return {"context": version_dict(after), "revision": revision + 1, "replayed": False}


compress_context = serialized_model_operation(_compress_context)


async def list_versions(project_id: str):
    async with database._get_session_maker()() as session:
        await require_project(session, project_id)
        versions = (
            await session.scalars(
                select(ContextVersion)
                .where(ContextVersion.project_id == project_id)
                .order_by(ContextVersion.created_at)
            )
        ).all()
        return [version_dict(v) for v in versions]


async def get_version(project_id: str, version_id: str):
    async with database._get_session_maker()() as session:
        await require_project(session, project_id)
        return version_dict(
            await require_version(session, project_id, version_id), include_messages=True
        )


async def restore_context(project_id: str, version_id: str, request_id: str, revision: int):
    async with database._get_session_maker()() as session:
        project = await require_project(session, project_id)
        source = await require_version(session, project_id, version_id)
        existing = await session.scalar(
            select(ContextVersion).where(
                ContextVersion.project_id == project_id,
                ContextVersion.request_id == request_id,
                ContextVersion.kind == "restored",
            )
        )
        if existing:
            if existing.parent_id != source.id:
                raise MemoryError("request_id уже использован для другого восстановления")
            return {
                "context": version_dict(existing),
                "revision": project.revision,
                "replayed": True,
            }
        check_revision(project, revision)
        messages, _, _, _ = await load_context(session, project, source)
        restored = ContextVersion(
            id=new_id(),
            project_id=project_id,
            request_id=request_id,
            parent_id=source.id,
            kind="restored",
            messages=messages,
            summary=source.summary,
            covered_message_id=source.covered_message_id,
            estimated_tokens=estimate_tokens(messages),
        )
        await advance(session, project_id, revision, current_context_id=restored.id)
        session.add(restored)
        await session.commit()
        return {"context": version_dict(restored), "revision": revision + 1, "replayed": False}
