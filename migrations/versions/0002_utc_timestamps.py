"""Repair timestamps from early, unversioned installations (stored as naive UTC)."""

import sqlalchemy as sa
from alembic import op

revision = "0002_utc_timestamps"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TABLES = ("projects", "project_messages", "context_versions", "plan_versions", "project_events")


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        column = next(
            c for c in sa.inspect(op.get_bind()).get_columns(table) if c["name"] == "created_at"
        )
        if isinstance(column["type"], sa.DateTime) and not column["type"].timezone:
            op.alter_column(
                table,
                "created_at",
                type_=sa.DateTime(timezone=True),
                postgresql_using="created_at AT TIME ZONE 'UTC'",
            )


def downgrade():
    # Revision 0001 also expects timezone-aware values. No lossy conversion on rollback.
    pass
