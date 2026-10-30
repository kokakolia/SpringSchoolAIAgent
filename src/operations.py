"""One durable request-id namespace for every project operation, including failed attempts."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src import database
from src.memory_models import ContextVersion, Message, OperationRecord, PlanVersion, ProjectEvent


def payload_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def legacy_binding(session, pid, request_id):
    plan = await session.scalar(
        select(PlanVersion.id).where(
            PlanVersion.project_id == pid, PlanVersion.request_id == request_id
        )
    )
    message = await session.scalar(
        select(Message.text).where(
            Message.project_id == pid, Message.request_id == request_id, Message.role == "user"
        )
    )
    if plan or message is not None:
        return ("plan_propose" if plan else "message_send"), {"text": message}
    version = await session.scalar(
        select(ContextVersion).where(
            ContextVersion.project_id == pid,
            ContextVersion.request_id == request_id,
            ContextVersion.kind.in_(["compressed", "restored"]),
        )
    )
    if version:
        return (
            ("context_restore", {"version_id": version.parent_id})
            if version.kind == "restored"
            else ("context_compress", {})
        )
    event = await session.scalar(
        select(ProjectEvent).where(
            ProjectEvent.project_id == pid, ProjectEvent.request_id == request_id
        )
    )
    if event:
        kind, payload = event.kind, event.payload
        if kind == "plan_accepted":
            return "plan_accept", {"plan_id": payload["plan_id"]}
        if kind == "time_lost":
            return "time_loss", {"minutes": payload["minutes"]}
        if kind == "task_changed":
            return "task_change", payload
        return "internal_event", payload
    return None


async def register(project_id, request_id, kind, payload):
    from src.memory import MemoryError, require_project

    request_id = str(request_id)
    fingerprint = payload_hash(payload)
    async with database._get_session_maker()() as session:
        await require_project(session, project_id)
        row = await session.get(OperationRecord, (project_id, request_id))
        if row is None:
            previous = await legacy_binding(session, project_id, request_id)
            if previous and (previous[0] != kind or payload_hash(previous[1]) != fingerprint):
                raise MemoryError(
                    "request_id уже использован другой операцией", code="request_id_conflict"
                )
            session.add(
                OperationRecord(
                    project_id=project_id,
                    request_id=request_id,
                    kind=kind,
                    payload_hash=fingerprint,
                )
            )
            try:
                await session.commit()
                return
            except IntegrityError:
                await session.rollback()
                row = await session.get(OperationRecord, (project_id, request_id))
        if row is None or row.kind != kind or row.payload_hash != fingerprint:
            raise MemoryError(
                "request_id уже использован другой операцией", code="request_id_conflict"
            )
