"""Sessions, stable requirement snapshots, operation namespace and indexed task dependencies."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0006_sessions_requirements"
down_revision = "0005_budget_and_archive"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in ("projects", "plan_versions", "project_task_archive"):
        columns = {col["name"] for col in sa.inspect(bind).get_columns(table)}
        if "requirements" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "requirements", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
                ),
            )
            if table == "projects":
                projects = sa.table(
                    "projects",
                    sa.column("id"),
                    sa.column("state", sa.JSON()),
                    sa.column("requirements", sa.JSON()),
                )
                for pid, state in bind.execute(sa.select(projects.c.id, projects.c.state)).all():
                    catalogue = [
                        {"id": str(uuid4()), "version": 1, "text": text}
                        for text in state.get("constraints", [])
                    ]
                    bind.execute(
                        projects.update().where(projects.c.id == pid).values(requirements=catalogue)
                    )
    tables = set(sa.inspect(bind).get_table_names())
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("auth_tag", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "project_operations" not in tables:
        op.create_table(
            "project_operations",
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
            sa.Column("request_id", sa.String(36), primary_key=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
        )
    if "project_requirement_proofs" not in tables:
        op.create_table(
            "project_requirement_proofs",
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
            sa.Column("task_id", sa.String(48), primary_key=True),
            sa.Column("requirement_id", sa.String(36), primary_key=True),
            sa.Column("version", sa.Integer(), primary_key=True),
            sa.Column("text", sa.Text(), nullable=False),
        )
    if "project_task_dependencies" not in tables:
        dependencies = op.create_table(
            "project_task_dependencies",
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
            sa.Column("task_id", sa.String(48), primary_key=True),
            sa.Column("dependency_id", sa.String(48), primary_key=True),
        )
        plans = sa.table(
            "plan_versions",
            sa.column("project_id"),
            sa.column("accepted_revision"),
            sa.column("proposal", sa.JSON()),
        )
        archives = sa.table(
            "project_task_archive", sa.column("project_id"), sa.column("task", sa.JSON())
        )
        edges = set()
        seen = set()
        for pid, proposal in bind.execute(
            sa.select(plans.c.project_id, plans.c.proposal)
            .where(plans.c.accepted_revision.is_not(None))
            .order_by(plans.c.accepted_revision.desc())
        ):
            if pid in seen:
                continue
            seen.add(pid)
            edges.update(
                (pid, t["id"], dep)
                for t in proposal.get("tasks", [])
                for dep in t.get("depends_on", [])
            )
        for pid, task in bind.execute(sa.select(archives.c.project_id, archives.c.task)):
            edges.update((pid, task["id"], dep) for dep in task.get("depends_on", []))
        for pid, task_id, dep in edges:
            bind.execute(
                dependencies.insert().values(project_id=pid, task_id=task_id, dependency_id=dep)
            )
    indexes = [
        ("project_messages", "ix_message_cursor", ["project_id", "id"]),
        ("plan_versions", "ix_plan_history", ["project_id", "created_at", "id"]),
        ("project_events", "ix_event_history", ["project_id", "created_at", "id"]),
        ("project_events", "ix_event_revision", ["project_id", "revision"]),
        (
            "project_requirement_proofs",
            "ix_requirement_credit",
            ["project_id", "requirement_id", "version"],
        ),
        ("project_task_dependencies", "ix_dependency_reverse", ["project_id", "dependency_id"]),
    ]
    for table, name, columns in indexes:
        if name not in {index["name"] for index in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade():
    for name in (
        "project_task_dependencies",
        "project_requirement_proofs",
        "project_operations",
        "auth_sessions",
    ):
        op.drop_table(name)
    for table, name in (
        ("project_messages", "ix_message_cursor"),
        ("plan_versions", "ix_plan_history"),
        ("project_events", "ix_event_history"),
        ("project_events", "ix_event_revision"),
    ):
        op.drop_index(name, table_name=table)
    for table in ("project_task_archive", "plan_versions", "projects"):
        op.drop_column(table, "requirements")
