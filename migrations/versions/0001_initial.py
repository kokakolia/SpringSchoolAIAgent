"""Initial schema; adopt existing installations without deleting their data."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("current_context_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if not sa.inspect(op.get_bind()).has_table("tasks"):
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("quadrant", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=1000), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("quadrant BETWEEN 1 AND 4"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not sa.inspect(op.get_bind()).has_table("context_versions"):
        op.create_table(
            "context_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("parent_id", sa.String(length=36), nullable=True),
            sa.Column("kind", sa.String(length=24), nullable=False),
            sa.Column("messages", sa.JSON(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("covered_message_id", sa.Integer(), nullable=False),
            sa.Column("estimated_tokens", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["projects.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "request_id", "kind"),
        )
    if "ix_context_versions_project_id" not in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes("context_versions")
    }:
        op.create_index(
            op.f("ix_context_versions_project_id"), "context_versions", ["project_id"], unique=False
        )
    if not sa.inspect(op.get_bind()).has_table("plan_versions"):
        op.create_table(
            "plan_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("base_revision", sa.Integer(), nullable=False),
            sa.Column("proposal", sa.JSON(), nullable=False),
            sa.Column("validation", sa.JSON(), nullable=False),
            sa.Column("accepted_revision", sa.Integer(), nullable=True),
            sa.Column("accept_request_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["projects.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "request_id"),
        )
    if "ix_plan_versions_project_id" not in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes("plan_versions")
    }:
        op.create_index(
            op.f("ix_plan_versions_project_id"), "plan_versions", ["project_id"], unique=False
        )
    if not sa.inspect(op.get_bind()).has_table("project_events"):
        op.create_table(
            "project_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["projects.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "request_id"),
        )
    if "ix_project_events_project_id" not in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes("project_events")
    }:
        op.create_index(
            op.f("ix_project_events_project_id"), "project_events", ["project_id"], unique=False
        )
    if not sa.inspect(op.get_bind()).has_table("project_messages"):
        op.create_table(
            "project_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("usage", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["projects.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "request_id", "role"),
        )
    if "ix_project_messages_project_id" not in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes("project_messages")
    }:
        op.create_index(
            op.f("ix_project_messages_project_id"), "project_messages", ["project_id"], unique=False
        )
    if not sa.inspect(op.get_bind()).has_table("project_task_progress"):
        op.create_table(
            "project_task_progress",
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("task_id", sa.String(length=48), nullable=False),
            sa.Column("done", sa.Boolean(), nullable=False),
            sa.Column("pinned", sa.Boolean(), nullable=False),
            sa.Column("spent_minutes", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["projects.id"],
            ),
            sa.PrimaryKeyConstraint("project_id", "task_id"),
        )


def downgrade():
    op.drop_table("project_task_progress")
    op.drop_table("project_messages")
    op.drop_table("project_events")
    op.drop_table("plan_versions")
    op.drop_table("context_versions")
    op.drop_table("tasks")
    op.drop_table("projects")
