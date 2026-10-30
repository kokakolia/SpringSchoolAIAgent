"""Separate time ledger and immutable task archive; preserve existing remaining balances."""

import sqlalchemy as sa
from alembic import op

revision = "0005_budget_and_archive"
down_revision = "0004_model_operations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("projects")}
    if "time_budget_minutes" not in columns:
        op.add_column("projects", sa.Column("time_budget_minutes", sa.BigInteger(), nullable=True))
        op.add_column(
            "projects",
            sa.Column("time_lost_minutes", sa.BigInteger(), nullable=False, server_default="0"),
        )
        op.add_column(
            "projects",
            sa.Column("time_spent_minutes", sa.BigInteger(), nullable=False, server_default="0"),
        )
        projects = sa.table(
            "projects",
            sa.column("id"),
            sa.column("state", sa.JSON()),
            sa.column("time_budget_minutes"),
            sa.column("time_lost_minutes"),
            sa.column("time_spent_minutes"),
        )
        progress = sa.table(
            "project_task_progress",
            sa.column("project_id"),
            sa.column("done"),
            sa.column("debited_minutes"),
        )
        events = sa.table(
            "project_events",
            sa.column("project_id"),
            sa.column("kind"),
            sa.column("payload", sa.JSON()),
        )
        spent = dict(
            bind.execute(
                sa.select(
                    progress.c.project_id,
                    sa.func.coalesce(sa.func.sum(progress.c.debited_minutes), 0),
                )
                .where(progress.c.done == sa.true())
                .group_by(progress.c.project_id)
            ).all()
        )
        lost = {}
        for pid, payload in bind.execute(
            sa.select(events.c.project_id, events.c.payload).where(events.c.kind == "time_lost")
        ):
            lost[pid] = lost.get(pid, 0) + payload["minutes"]
        for pid, state in bind.execute(sa.select(projects.c.id, projects.c.state)).all():
            remaining = state.get("available_minutes")
            # Old clamped balances cannot reveal historical overrun; do not invent it.
            bind.execute(
                projects.update()
                .where(projects.c.id == pid)
                .values(
                    time_budget_minutes=None
                    if remaining is None
                    else remaining + lost.get(pid, 0) + spent.get(pid, 0),
                    time_lost_minutes=lost.get(pid, 0),
                    time_spent_minutes=spent.get(pid, 0),
                )
            )
    if not sa.inspect(bind).has_table("project_task_archive"):
        op.create_table(
            "project_task_archive",
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
            sa.Column("task_id", sa.String(48), primary_key=True),
            sa.Column("task", sa.JSON(), nullable=False),
            sa.Column("constraints", sa.JSON(), nullable=False),
            sa.Column("source_plan_id", sa.String(36), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    op.drop_table("project_task_archive")
    for name in ("time_spent_minutes", "time_lost_minutes", "time_budget_minutes"):
        op.drop_column("projects", name)
