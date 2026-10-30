"""Proposal -> deterministic validation -> explicit acceptance, with a complete audit trail."""

import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from src import database, memory
from src.context_budget import ContextGuard
from src.llm import ModelUnavailable, YandexClient
from src.memory_models import (
    ArchivedTask,
    Message,
    PlanVersion,
    ProjectEvent,
    RequirementProof,
    TaskProgress,
    iso_utc,
)
from src.model_operations import serialized_model_operation
from src.operations import register
from src.plan_schemas import ChangeTask, LoseTime, PlanProposal, ProposePlan
from src.planner import plan_diff, same_plan, validate_plan
from src.requirements import bind, key
from src.task_integrity import (
    broken_completed_dependencies,
    completed_dependents,
    confirmed_requirements,
    progress_map,
    replace_dependencies,
    task_ids,
    unfinished_ancestors,
)
from src.time_budget import change_budget


async def generate_checked(
    client, messages, state, flags, baseline, guard=None, checker=None, requirements=None
):
    checks, usages = [], []
    proposal = None
    validation = None
    for attempt in range(1, 4):
        try:
            if guard is not None:
                messages = await guard.prepare(messages)
            else:
                count = await client.count_tokens(messages)
                if memory.metrics(messages, count)["budget_fraction"] > 1:
                    raise memory.MemoryError("Запрос превысил бюджет контекста", 413)
        except memory.MemoryError as error:
            if attempt == 1 or error.status != 413:
                raise
            break
        text, usage = await client.complete(messages, json_mode=True)
        usages.append(usage)
        try:
            proposal = PlanProposal.model_validate_json(text)
            if requirements is not None:
                bind(proposal, requirements, flags)
            validation = (
                await checker(proposal)
                if checker
                else validate_plan(proposal, state, flags, baseline)
            )
            errors = validation["errors"]
        except ValueError as error:
            proposal = None
            detail = str(error)
            if hasattr(error, "errors"):
                detail = "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                    for e in error.errors(include_input=False, include_url=False)
                )
            errors = [{"code": "invalid_schema", "message": detail[:4000]}]
        checks.append({"attempt": attempt, "errors": errors})
        if proposal is not None and (
            validation["valid"]
            or any(
                e["code"]
                in {"questions", "capacity_unknown", "deadline_unknown", "success_unknown"}
                for e in errors
            )
        ):
            break
        if attempt == 3:
            break
        feedback = """Сервер проверил предложение и обнаружил ошибки ниже.
Исправь план и верни полный JSON по исходной схеме. Опирайся на подтверждённые факты.
Если времени не хватает, явно отложи необязательное или сузь объём с decision=simplify
и объясни последствия. Не уменьшай оценки без обоснованного изменения объёма.
Ссылки constraint_refs только из списка numbered_constraints. Отметь все вопросы,
которые мешают предложить выполнимый вариант, в questions. Не утверждай, что план
помещается во время, если сервер считает иначе.
Если превышен бюджет: сумма estimate_minutes всех активных невыполненных задач
плюс buffer_minutes должна быть НЕ БОЛЬШЕ доступного времени. Разрешено упростить
презентацию до краткого рассказа, оставить один сценарий интерфейса, отложить README;
выбери подходящие проекту компромиссы и отрази сокращение объёма в done_when и tradeoff.
Ошибки: """
        budget = {
            "available_minutes": state.get("available_minutes"),
            "required_minutes": validation["required_minutes"] if proposal else None,
        }
        revised = messages + [
            {"role": "assistant", "text": text},
            {
                "role": "user",
                "text": feedback
                + json.dumps(errors, ensure_ascii=False)
                + "\nБюджет: "
                + json.dumps(budget),
            },
        ]
        messages = revised
    if proposal is None:
        raise ModelUnavailable("Модель вернула план неверного формата. Повторите запрос")
    validation["generation_checks"] = checks
    return (
        proposal,
        validation,
        {"requests": usages, "context_checks": guard.checks if guard else []},
    )


async def head_plan(session, project_id):
    return await session.scalar(
        select(PlanVersion)
        .where(PlanVersion.project_id == project_id, PlanVersion.accepted_revision.is_not(None))
        .order_by(PlanVersion.accepted_revision.desc())
        .limit(1)
    )


def proposal_is_current(row, project, events):
    delta = project.revision - (row.base_revision + 1)
    if delta < 0:
        return False
    newer = [e for e in events if row.base_revision + 1 < e.revision <= project.revision]
    return len({e.revision for e in newer}) == delta and all(
        e.kind == "project_renamed" for e in newer
    )


def plan_dict(row):
    if row is None:
        return None
    return {
        "id": row.id,
        "proposal": {**row.proposal, "archive_task_ids": []}
        if row.accepted_revision is not None
        else row.proposal,
        "validation": row.validation,
        "base_revision": row.base_revision,
        "accepted_revision": row.accepted_revision,
        "created_at": iso_utc(row.created_at),
    }


async def proposal_current(session, row, project):
    delta = project.revision - row.base_revision - 1
    if delta < 0:
        return False
    if delta == 0:
        return True
    query = select(
        func.count(func.distinct(ProjectEvent.revision)),
        func.count().filter(ProjectEvent.kind != "project_renamed"),
    ).where(
        ProjectEvent.project_id == project.id,
        ProjectEvent.revision > row.base_revision + 1,
        ProjectEvent.revision <= project.revision,
    )
    count, other = (await session.execute(query)).one()
    return count == delta and other == 0


async def checked(session, project, proposal, baseline):
    flags = await progress_map(session, project.id, task_ids(proposal.model_dump(), baseline))
    return validate_plan(
        proposal,
        project.state,
        flags,
        baseline,
        requirements=project.requirements,
        confirmed=await confirmed_requirements(session, project.id, project.requirements),
        broken_dependencies=await broken_completed_dependencies(session, project.id),
    )


async def workspace(project_id):
    async with database._get_session_maker()() as session:
        project = await memory.require_project(session, project_id)
        head = await head_plan(session, project_id)
        latest = await session.scalar(
            select(PlanVersion)
            .where(PlanVersion.project_id == project_id)
            .order_by(PlanVersion.created_at.desc(), PlanVersion.id.desc())
            .limit(1)
        )
        draft = latest if latest and latest.accepted_revision is None else None
        if (
            draft
            and head
            and draft.validation["valid"]
            and same_plan(plan_dict(head)["proposal"], draft.proposal)
        ):
            draft = None
        accepted = plan_dict(head)
        ids = task_ids(
            accepted["proposal"] if accepted else None, draft.proposal if draft else None
        )
        flags = await progress_map(session, project_id, ids)
        validation = (
            await checked(
                session,
                project,
                PlanProposal.model_validate(accepted["proposal"]),
                accepted["proposal"],
            )
            if head
            else None
        )
        archive_count = await session.scalar(
            select(func.count())
            .select_from(ArchivedTask)
            .where(ArchivedTask.project_id == project_id)
        )
        return {
            "project": memory.project_dict(project),
            "accepted": accepted,
            "draft": plan_dict(draft),
            "progress": {
                tid: {
                    k: v
                    for k, v in value.items()
                    if k in {"done", "pinned", "spent_minutes", "archived"}
                }
                for tid, value in flags.items()
            },
            "diff": plan_diff(accepted["proposal"] if accepted else None, draft.proposal)
            if draft
            else [],
            "draft_stale": bool(draft and not await proposal_current(session, draft, project)),
            "current_validation": validation,
            "archive_count": archive_count,
            "context_usage": await memory.context_metrics(session, project),
            "server_now": datetime.now(timezone.utc).isoformat(),
        }


@serialized_model_operation
async def propose(project_id: str, body: ProposePlan, client: YandexClient):
    await register(project_id, body.request_id, "plan_propose", {"text": body.text})
    async with database._get_session_maker()() as session:
        project = await memory.require_project(session, project_id)
        existing = await session.scalar(
            select(PlanVersion).where(
                PlanVersion.project_id == project_id, PlanVersion.request_id == str(body.request_id)
            )
        )
        if existing:
            user = await session.scalar(
                select(Message).where(
                    Message.project_id == project_id,
                    Message.request_id == str(body.request_id),
                    Message.role == "user",
                )
            )
            if user is None or user.text != body.text:
                raise memory.MemoryError("request_id уже использован для другого предложения")
            return {"plan": plan_dict(existing), "revision": project.revision, "replayed": True}
        memory.check_revision(project, body.base_revision)
        guard = await ContextGuard.load(session, project, str(body.request_id), client)
        messages = list(guard.context)
        head = await head_plan(session, project_id)
        latest = await session.scalar(
            select(PlanVersion)
            .where(PlanVersion.project_id == project_id)
            .order_by(PlanVersion.created_at.desc())
            .limit(1)
        )
        state = project.state
        baseline = plan_dict(head)["proposal"] if head else None
        flags = await progress_map(
            session, project_id, task_ids(baseline, latest.proposal if latest else None)
        )
        requirement_snapshot = project.requirements
        # A disputed draft is retained as input even before the first acceptance.
        reference = plan_dict(latest)["proposal"] if latest else baseline
        events = (
            await session.scalars(
                select(ProjectEvent)
                .where(ProjectEvent.project_id == project_id)
                .order_by(ProjectEvent.revision.desc())
                .limit(10)
            )
        ).all()
        evidence = {
            "accepted_plan": baseline,
            "latest_proposal": reference,
            "progress": {key: value for key, value in flags.items() if not value.get("archived")},
            "new_task_id_prefix": "t_" + str(body.request_id).split("-")[0] + "_",
            "events": [{"kind": e.kind, "payload": e.payload} for e in events],
            "now": datetime.now(timezone.utc).isoformat(),
            "numbered_constraints": [
                {"ref": index, **item} for index, item in enumerate(requirement_snapshot, 1)
            ],
        }
    instruction = """Подготовь предложение плана. Ответ — только JSON по схеме ниже.
Дай 4–8 компактных задач, если это возможно. Оценки длительности — явно указанные предположения.
Сохраняй id существующих задач. Для новых id используй new_task_id_prefix и короткий суффикс.
Завершённые и ранее отложенные незакреплённые задачи можно явно перечислить в archive_task_ids,
чтобы убрать из текущих tasks и освободить лимит 30.
Активную или закреплённую задачу архивировать нельзя.
Архив не удаляет историю; пользователь отдельно принимает такое предложение.
Остальные задачи принятого плана должны остаться в ответе;
исключение показывай decision=defer с reason и tradeoff. Для закреплённых и выполненных
сохрани title, done_when и decision=keep. Не меняй подтверждённые факты проекта.
Для активных задач укажи зависимости; не ссылайся на отложенные. Весь оставшийся объём
с buffer_minutes должен помещаться в available_minutes и доступные интервалы до дедлайна.
requirement_refs — ссылки {id, version} из numbered_constraints. Для выполненных задач
сохрани исходные ссылки: новая версия требования требует новой незавершённой работы.
constraint_refs — прежние позиционные ссылки для совместимости. Каждое требование
должно быть отражено хотя бы в одной неотложенной задаче. Сокращения объясняй:
почему, что выигрываем и что теряем. Резерв по возможности сохраняй. Если обязательный
объём не помещается, объясни конфликт в questions, не скрывай его уменьшением оценок.
При нехватке важных данных задай questions. Пиши на русском, коротко, без markdown.
Схема: """ + json.dumps(PlanProposal.model_json_schema(), ensure_ascii=False)
    messages[0] = {"role": "system", "text": messages[0]["text"] + "\n" + instruction}
    messages += [
        {
            "role": "user",
            "text": "Состояние работ и события:\n" + json.dumps(evidence, ensure_ascii=False),
        },
        {"role": "user", "text": body.text},
    ]

    async def checker(value):
        async with database._get_session_maker()() as session:
            return await checked(session, project, value, baseline)

    proposal, validation, usage = await generate_checked(
        client, messages, state, flags, baseline, guard, checker, requirement_snapshot
    )
    validation["unchanged"] = bool(
        validation["valid"] and same_plan(baseline, proposal.model_dump())
    )
    reply = proposal.summary
    if not validation["valid"]:
        reply = (
            "План не прошёл проверку: "
            + "; ".join(error["message"] for error in validation["errors"])
            + "\n\nПредложение агента: "
            + reply
        )
    async with database._get_session_maker()() as session:
        await memory.advance(session, project_id, body.base_revision, **guard.persist(session))
        row = PlanVersion(
            project_id=project_id,
            request_id=str(body.request_id),
            base_revision=body.base_revision,
            proposal=proposal.model_dump(),
            requirements=list(
                {
                    key(item): item
                    for item in [*(head.requirements if head else []), *requirement_snapshot]
                    if key(item)
                    in (
                        {key(ref) for task in proposal.tasks for ref in task.requirement_refs}
                        | {key(item) for item in requirement_snapshot}
                    )
                }.values()
            ),
            validation=validation,
        )
        session.add(row)
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
                    text=reply
                    + ("\nВопросы: " + "; ".join(proposal.questions) if proposal.questions else ""),
                    usage=usage,
                ),
            ]
        )
        await session.commit()
        return {
            "plan": plan_dict(row),
            "revision": body.base_revision + 1,
            "replayed": False,
            "usage": usage,
        }


async def accept(project_id, plan_id, body):
    await register(project_id, body.request_id, "plan_accept", {"plan_id": plan_id})
    async with database._get_session_maker()() as session:
        project = await memory.require_project(session, project_id)
        row = await session.get(PlanVersion, plan_id)
        if row is None or row.project_id != project_id:
            raise memory.MemoryError("Предложение не найдено", 404)
        previous_event = await session.scalar(
            select(ProjectEvent).where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.request_id == str(body.request_id),
            )
        )
        if previous_event and (
            previous_event.kind != "plan_accepted"
            or previous_event.payload.get("plan_id") != plan_id
        ):
            raise memory.MemoryError("request_id уже использован для другого события")
        if row.accepted_revision is not None:
            if row.accept_request_id == str(body.request_id):
                return {"plan": plan_dict(row), "revision": project.revision, "replayed": True}
            raise memory.MemoryError("Эта версия уже была принята")
        memory.check_revision(project, body.base_revision)
        if not await proposal_current(session, row, project):
            raise memory.MemoryError("Обстоятельства изменились. Подготовьте новый план")
        head = await head_plan(session, project_id)
        validation = await checked(
            session,
            project,
            PlanProposal.model_validate(row.proposal),
            plan_dict(head)["proposal"] if head else None,
        )
        if not validation["valid"]:
            raise memory.MemoryError(
                "План не прошёл проверку: " + "; ".join(e["message"] for e in validation["errors"]),
                422,
            )
        if head and same_plan(plan_dict(head)["proposal"], row.proposal):
            raise memory.MemoryError("План не изменился; повторное принятие не требуется")
        await memory.advance(session, project_id, body.base_revision)
        old_tasks = {task["id"]: task for task in (head.proposal if head else {}).get("tasks", [])}
        for task_id in row.proposal.get("archive_task_ids", []):
            session.add(
                ArchivedTask(
                    project_id=project_id,
                    task_id=task_id,
                    task=old_tasks[task_id],
                    constraints=[
                        item["text"]
                        for item in head.requirements
                        if key(item)
                        in {key(ref) for ref in old_tasks[task_id].get("requirement_refs", [])}
                    ],
                    requirements=[
                        item
                        for item in head.requirements
                        if key(item)
                        in {key(ref) for ref in old_tasks[task_id].get("requirement_refs", [])}
                    ],
                    source_plan_id=head.id,
                )
            )
        await replace_dependencies(session, project_id, row.proposal)
        row.accepted_revision = body.base_revision + 1
        row.accept_request_id = str(body.request_id)
        session.add(
            ProjectEvent(
                project_id=project_id,
                request_id=str(body.request_id),
                kind="plan_accepted",
                revision=body.base_revision + 1,
                payload={
                    "plan_id": row.id,
                    "archived_task_ids": row.proposal.get("archive_task_ids", []),
                },
            )
        )
        await session.commit()
        return {"plan": plan_dict(row), "revision": body.base_revision + 1, "replayed": False}


async def lose_time(project_id, body: LoseTime):
    await register(project_id, body.request_id, "time_loss", {"minutes": body.minutes})
    async with database._get_session_maker()() as session:
        project = await memory.require_project(session, project_id)
        old = await session.scalar(
            select(ProjectEvent).where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.request_id == str(body.request_id),
            )
        )
        if old:
            if old.kind != "time_lost" or old.payload["minutes"] != body.minutes:
                raise memory.MemoryError("request_id уже использован для другого события")
            return {"revision": project.revision, "replayed": True}
        memory.check_revision(project, body.base_revision)
        if project.state.get("available_minutes") is None:
            raise memory.MemoryError("Сначала укажите доступное время", 422)
        budget_values = change_budget(project, lost_delta=body.minutes)
        state = budget_values["state"]
        await memory.advance(session, project_id, body.base_revision, **budget_values)
        session.add(
            ProjectEvent(
                project_id=project_id,
                request_id=str(body.request_id),
                kind="time_lost",
                revision=body.base_revision + 1,
                payload={"minutes": body.minutes, "remaining_minutes": state["available_minutes"]},
            )
        )
        await session.commit()
        return {"revision": body.base_revision + 1, "replayed": False}


async def change_task(project_id, task_id, body: ChangeTask):
    payload = {"task_id": task_id, **body.model_dump(exclude={"request_id", "base_revision"})}
    await register(project_id, body.request_id, "task_change", payload)
    async with database._get_session_maker()() as session:
        project = await memory.require_project(session, project_id)
        payload = {"task_id": task_id, **body.model_dump(exclude={"request_id", "base_revision"})}
        old = await session.scalar(
            select(ProjectEvent).where(
                ProjectEvent.project_id == project_id,
                ProjectEvent.request_id == str(body.request_id),
            )
        )
        if old:
            if old.kind != "task_changed" or old.payload != payload:
                raise memory.MemoryError("request_id уже использован для другого события")
            return {"revision": project.revision, "replayed": True}
        memory.check_revision(project, body.base_revision)
        head = await head_plan(session, project_id)
        task = (
            next((t for t in head.proposal["tasks"] if t["id"] == task_id), None) if head else None
        )
        if task is None or task["decision"] == "defer":
            raise memory.MemoryError("Активная задача принятого плана не найдена", 404)
        progress = await session.get(TaskProgress, (project_id, task_id))
        if progress is None:
            progress = TaskProgress(
                project_id=project_id, task_id=task_id, done=False, pinned=False, spent_minutes=0
            )
        spent_delta = 0
        if body.done is True and not progress.done:
            if await unfinished_ancestors(session, project_id, task_id):
                raise memory.MemoryError("Сначала завершите задачи, от которых зависит эта", 422)
            spent = (
                body.actual_minutes if body.actual_minutes is not None else task["estimate_minutes"]
            )
            await session.execute(
                delete(RequirementProof).where(
                    RequirementProof.project_id == project_id, RequirementProof.task_id == task_id
                )
            )
            refs = {key(ref) for ref in task.get("requirement_refs", [])}
            session.add_all(
                [
                    RequirementProof(
                        project_id=project_id,
                        task_id=task_id,
                        requirement_id=item["id"],
                        version=item["version"],
                        text=item["text"],
                    )
                    for item in head.requirements
                    if key(item) in refs
                ]
            )
            progress.spent_minutes = spent
            progress.done = True
            progress.debited_minutes = 0
            if project.time_budget_minutes is not None:
                progress.debited_minutes = spent
                spent_delta = spent
        elif body.done is False and progress.done:
            if await completed_dependents(session, project_id, task_id):
                raise memory.MemoryError("Сначала верните в работу зависимые задачи", 422)
            spent_delta = -(progress.debited_minutes or 0)
            progress.done, progress.spent_minutes, progress.debited_minutes = False, 0, 0
        if body.pinned is not None:
            progress.pinned = body.pinned
        await memory.advance(
            session,
            project_id,
            body.base_revision,
            **change_budget(project, spent_delta=spent_delta),
        )
        session.add(progress)
        session.add(
            ProjectEvent(
                project_id=project_id,
                request_id=str(body.request_id),
                kind="task_changed",
                revision=body.base_revision + 1,
                payload=payload,
            )
        )
        await session.commit()
        return {"revision": body.base_revision + 1, "replayed": False}


async def task_archive(project_id, offset=0, limit=30):
    async with database._get_session_maker()() as session:
        await memory.require_project(session, project_id)
        total = await session.scalar(
            select(func.count())
            .select_from(ArchivedTask)
            .where(ArchivedTask.project_id == project_id)
        )
        rows = (
            await session.scalars(
                select(ArchivedTask)
                .where(ArchivedTask.project_id == project_id)
                .order_by(ArchivedTask.archived_at.desc(), ArchivedTask.task_id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return {
            "total": total,
            "items": [
                {
                    "task": row.task,
                    "source_plan_id": row.source_plan_id,
                    "archived_at": iso_utc(row.archived_at),
                }
                for row in rows
            ],
        }
