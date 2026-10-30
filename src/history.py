"""History is a bounded summary page; complete plans are loaded only by explicit ID."""

from sqlalchemy import literal, select, union_all

from src import database, memory
from src.memory_models import PlanVersion, ProjectEvent, iso_utc


async def page(project_id, offset=0, limit=30):
    async with database._get_session_maker()() as session:
        await memory.require_project(session, project_id)
        timeline = union_all(
            select(PlanVersion.id, literal("plan").label("source"), PlanVersion.created_at).where(
                PlanVersion.project_id == project_id
            ),
            select(
                ProjectEvent.id, literal("event").label("source"), ProjectEvent.created_at
            ).where(ProjectEvent.project_id == project_id),
        ).subquery()
        selected = (
            await session.execute(
                select(timeline)
                .order_by(timeline.c.created_at.desc(), timeline.c.id.desc(), timeline.c.source)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        more = len(selected) > limit
        selected = selected[:limit]
        plan_ids = [row.id for row in selected if row.source == "plan"]
        event_ids = [row.id for row in selected if row.source == "event"]
        details = {}
        for row in (
            await session.execute(
                select(
                    PlanVersion.id,
                    PlanVersion.proposal["summary"].as_string().label("summary"),
                    PlanVersion.accepted_revision,
                ).where(PlanVersion.id.in_(plan_ids), PlanVersion.project_id == project_id)
            )
        ).all():
            details[("plan", row.id)] = {
                "title": "Принятая версия плана"
                if row.accepted_revision is not None
                else "Предложение агента",
                "text": row.summary,
            }
        payload = ProjectEvent.payload
        query = select(
            ProjectEvent.id,
            ProjectEvent.kind,
            payload["minutes"].as_integer().label("minutes"),
            payload["remaining_minutes"].as_integer().label("remaining"),
            payload["title"].as_string().label("title"),
            payload["task_id"].as_string().label("task_id"),
            payload["done"].as_boolean().label("done"),
            payload["pinned"].as_boolean().label("pinned"),
        ).where(ProjectEvent.id.in_(event_ids), ProjectEvent.project_id == project_id)
        titles = {
            "project_renamed": "Проект переименован",
            "state_updated": "Новые условия",
            "time_lost": "Изменилось доступное время",
            "task_changed": "Изменение задачи",
            "plan_accepted": "План принят",
        }
        for row in (await session.execute(query)).all():
            text = (
                "Уточнены цель, время или требования."
                if row.kind == "state_updated"
                else "Пользователь принял план."
            )
            if row.kind == "project_renamed":
                text = "Новое название: " + row.title
            if row.kind == "time_lost":
                text = f"Потеряно {row.minutes} мин. Осталось {row.remaining} мин."
            if row.kind == "task_changed":
                text = f"{row.task_id}: " + (
                    "завершена"
                    if row.done is True
                    else "вернулась в работу"
                    if row.done is False
                    else "закреплена"
                    if row.pinned
                    else "откреплена"
                )
            details[("event", row.id)] = {"title": titles.get(row.kind, "Событие"), "text": text}
        return {
            "items": [
                {
                    "id": row.source + ":" + row.id,
                    "created_at": iso_utc(row.created_at),
                    **details[(row.source, row.id)],
                }
                for row in selected
            ],
            "has_more": more,
            "next_offset": offset + len(selected),
        }


async def plan(project_id, plan_id):
    from src.plans import plan_dict

    async with database._get_session_maker()() as session:
        await memory.require_project(session, project_id)
        row = await session.get(PlanVersion, plan_id)
        if row is None or row.project_id != project_id:
            raise memory.MemoryError("План не найден", 404)
        return plan_dict(row)
