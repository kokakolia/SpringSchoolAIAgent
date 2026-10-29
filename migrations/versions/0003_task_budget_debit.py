"""Remember the actual budget debit separately from time spent."""

import sqlalchemy as sa
from alembic import op

revision = "0003_task_budget_debit"
down_revision = "0002_utc_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("project_task_progress")}
    if "debited_minutes" not in columns:
        op.add_column(
            "project_task_progress", sa.Column("debited_minutes", sa.Integer(), nullable=True)
        )
        # Earlier versions did not record the debit. Preserve their existing refund behavior.
        op.execute(
            sa.text(
                "UPDATE project_task_progress SET debited_minutes = "
                "CASE WHEN done THEN spent_minutes ELSE 0 END"
            )
        )


def downgrade():
    op.drop_column("project_task_progress", "debited_minutes")
