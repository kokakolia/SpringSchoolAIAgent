"""Persistent project state, original dialogue and immutable context versions."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    # SQLite drops timezone metadata; all audit timestamps are written in UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[dict] = mapped_column(JSON)
    requirements: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    time_budget_minutes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    time_lost_minutes: Mapped[int] = mapped_column(BigInteger, default=0)
    time_spent_minutes: Mapped[int] = mapped_column(BigInteger, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    current_context_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelOperation(Base):
    __tablename__ = "project_model_operations"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "project_messages"
    __table_args__ = (
        UniqueConstraint("project_id", "request_id", "role"),
        Index("ix_message_cursor", "project_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContextVersion(Base):
    __tablename__ = "context_versions"
    __table_args__ = (UniqueConstraint("project_id", "request_id", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    # Exact inputs at capture time; facts are rebuilt from canonical state on future turns.
    messages: Mapped[list] = mapped_column(JSON)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    covered_message_id: Mapped[int] = mapped_column(Integer, default=0)
    estimated_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "request_id"),
        Index("ix_plan_history", "project_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    base_revision: Mapped[int] = mapped_column(Integer)
    proposal: Mapped[dict] = mapped_column(JSON)
    requirements: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    validation: Mapped[dict] = mapped_column(JSON)
    accepted_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accept_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskProgress(Base):
    __tablename__ = "project_task_progress"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    done: Mapped[bool] = mapped_column(default=False)
    pinned: Mapped[bool] = mapped_column(default=False)
    spent_minutes: Mapped[int] = mapped_column(Integer, default=0)
    debited_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ArchivedTask(Base):
    __tablename__ = "project_task_archive"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    task: Mapped[dict] = mapped_column(JSON)
    constraints: Mapped[list] = mapped_column(JSON)
    requirements: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    source_plan_id: Mapped[str] = mapped_column(String(36))
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectEvent(Base):
    __tablename__ = "project_events"
    __table_args__ = (
        UniqueConstraint("project_id", "request_id"),
        Index("ix_event_history", "project_id", "created_at", "id"),
        Index("ix_event_revision", "project_id", "revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    auth_tag: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credential_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class AuthCredentials(Base):
    __tablename__ = "auth_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    salt: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(BigInteger)


class OperationRecord(Base):
    __tablename__ = "project_operations"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))


class RequirementProof(Base):
    __tablename__ = "project_requirement_proofs"
    __table_args__ = (Index("ix_requirement_credit", "project_id", "requirement_id", "version"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)


class TaskDependency(Base):
    __tablename__ = "project_task_dependencies"
    __table_args__ = (Index("ix_dependency_reverse", "project_id", "dependency_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    dependency_id: Mapped[str] = mapped_column(String(48), primary_key=True)
