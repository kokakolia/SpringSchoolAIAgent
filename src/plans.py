"""Proposal -> deterministic validation -> explicit acceptance, with a complete audit trail."""

import json
from datetime import datetime, timezone

from sqlalchemy import select

from src import database, memory
from src.context_budget import ContextGuard
from src.llm import ModelUnavailable, YandexClient
from src.memory_models import Message, PlanVersion, ProjectEvent, TaskProgress, iso_utc
from src.model_operations import serialized_model_operation
from src.plan_schemas import ChangeTask, LoseTime, PlanProposal, ProposePlan
from src.planner import plan_diff, same_plan, validate_plan


async def generate_checked(client, messages, state, flags, baseline, guard=None):
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
            validation = validate_plan(proposal, state, flags, baseline)
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


async def progress_map(session, project_id):
    rows = (
        await session.scalars(select(TaskProgress).where(TaskProgress.project_id == project_id))
    ).all()
    return {
        row.task_id: {"done": row.done, "pinned": row.pinned, "spent_minutes": row.spent_minutes}
        for row in rows
    }


def plan_dict(row):
    if row is None:
        return None
    return {
        "id": row.id,
        "proposal": row.proposal,
        "validation": row.validation,
        "base_revision": row.base_revision,
        "accepted_revision": row.accepted_revision,
        "created_at": iso_utc(row.created_at),
    }


async def workspace(project_id):
    async with database._get_session_maker()() as session:
        project = await memory.require_project(session, project_id)
        rows = (
            await session.scalars(
                select(PlanVersion)
                .where(PlanVersion.project_id == project_id)
                .order_by(PlanVersion.created_at.desc())
            )
        ).all()
        head = await head_plan(session, project_id)
        draft = rows[0] if rows and rows[0].accepted_revision is None else None
        if (
            draft
            and head
            and draft.validation["valid"]
            and same_plan(head.proposal, draft.proposal)
        ):
            draft = None
        flags = await progress_map(session, project_id)
        events = (
            await session.scalars(
                select(ProjectEvent)
                .where(ProjectEvent.project_id == project_id)
                .order_by(ProjectEvent.revision.desc())
            )
        ).all()
        messages, _, _, _ = await memory.load_context(session, project)
        current_validation = (
            validate_plan(
                PlanProposal.model_validate(head.proposal), project.state, flags, head.proposal
            )
            if head
            else None
        )
        return {
            "project": memory.project_dict(project),
            "accepted": plan_dict(head),
            "draft": plan_dict(draft),
            "progress": flags,
            "diff": plan_diff(head.proposal if head else None, draft.proposal) if draft else [],
            "draft_stale": bool(draft and not proposal_is_current(draft, project, events)),
            "current_validation": current_validation,
            "plans": [plan_dict(row) for row in rows],
            "events": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "payload": e.payload,
                    "revision": e.revision,
                    "created_at": iso_utc(e.created_at),
                }
                for e in events
            ],
            "context_usage": memory.metrics(messages),
            "server_now": datetime.now(timezone.utc).isoformat(),
        }


@serialized_model_operation
async def propose(project_id: str, body: ProposePlan, client: YandexClient):
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
        flags = await progress_map(session, project_id)
        state = project.state
        baseline = head.proposal if head else None
        # A disputed draft is retained as input even before the first acceptance.
        reference = latest.proposal if latest else baseline
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
            "progress": flags,
            "events": [{"kind": e.kind, "payload": e.payload} for e in events],
            "now": datetime.now(timezone.utc).isoformat(),
            "numbered_constraints": [
                {"ref": index, "text": text}
                for index, text in enumerate(state.get("constraints", []), 1)
            ],
        }
    instruction = """Подготовь предложение плана. Ответ — только JSON по схеме ниже.
Дай 4–8 компактных задач, если это возможно. Оценки длительности — явно указанные предположения.
Сохраняй id существующих задач. Все задачи принятого плана должны остаться в ответе;
исключение показывай decision=defer с reason и tradeoff. Для закреплённых и выполненных
сохрани title, done_when и decision=keep. Не меняй подтверждённые факты проекта.
Для активных задач укажи зависимости; не ссылайся на отложенные. Весь оставшийся объём
с buffer_minutes должен помещаться в available_minutes и доступные интервалы до дедлайна.
constraint_refs — номера подтверждённых constraints, начиная с 1. Каждое требование
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
    proposal, validation, usage = await generate_checked(
        client, messages, state, flags, baseline, guard
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
        events = (
            await session.scalars(
                select(ProjectEvent).where(
                    ProjectEvent.project_id == project_id,
                    ProjectEvent.revision > row.base_revision + 1,
                )
            )
        ).all()
        if not proposal_is_current(row, project, events):
            raise memory.MemoryError("Обстоятельства изменились. Подготовьте новый план")
        flags = await progress_map(session, project_id)
        head = await head_plan(session, project_id)
        validation = validate_plan(
            PlanProposal.model_validate(row.proposal),
            project.state,
            flags,
            head.proposal if head else None,
        )
        if not validation["valid"]:
            raise memory.MemoryError(
                "План не прошёл проверку: " + "; ".join(e["message"] for e in validation["errors"]),
                422,
            )
        if head and same_plan(head.proposal, row.proposal):
            raise memory.MemoryError("План не изменился; повторное принятие не требуется")
        await memory.advance(session, project_id, body.base_revision)
        row.accepted_revision = body.base_revision + 1
        row.accept_request_id = str(body.request_id)
        session.add(
            ProjectEvent(
                project_id=project_id,
                request_id=str(body.request_id),
                kind="plan_accepted",
                revision=body.base_revision + 1,
                payload={"plan_id": row.id},
            )
        )
        await session.commit()
        return {"plan": plan_dict(row), "revision": body.base_revision + 1, "replayed": False}


async def lose_time(project_id, body: LoseTime):
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
        state = {
            **project.state,
            "available_minutes": max(0, project.state["available_minutes"] - body.minutes),
        }
        await memory.advance(session, project_id, body.base_revision, state=state)
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
        flags = await progress_map(session, project_id)
        progress = await session.get(TaskProgress, (project_id, task_id))
        if progress is None:
            progress = TaskProgress(
                project_id=project_id, task_id=task_id, done=False, pinned=False, spent_minutes=0
            )
            session.add(progress)
        state = dict(project.state)
        if body.done is True and not progress.done:
            if any(not flags.get(dep, {}).get("done") for dep in task["depends_on"]):
                raise memory.MemoryError("Сначала завершите задачи, от которых зависит эта", 422)
            spent = (
                body.actual_minutes if body.actual_minutes is not None else task["estimate_minutes"]
            )
            progress.spent_minutes = spent
            progress.done = True
            progress.debited_minutes = 0
            if state.get("available_minutes") is not None:
                progress.debited_minutes = min(state["available_minutes"], spent)
                state["available_minutes"] -= progress.debited_minutes
        elif body.done is False and progress.done:
            if any(
                flags.get(t["id"], {}).get("done") and task_id in t["depends_on"]
                for t in head.proposal["tasks"]
            ):
                raise memory.MemoryError("Сначала верните в работу зависимые задачи", 422)
            if state.get("available_minutes") is not None:
                state["available_minutes"] += (
                    progress.debited_minutes
                    if progress.debited_minutes is not None
                    else progress.spent_minutes
                )
            progress.done, progress.spent_minutes, progress.debited_minutes = False, 0, 0
        if body.pinned is not None:
            progress.pinned = body.pinned
        await memory.advance(session, project_id, body.base_revision, state=state)
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
