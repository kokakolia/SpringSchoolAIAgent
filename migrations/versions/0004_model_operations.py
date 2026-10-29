"""Cross-process model operation leases."""

import sqlalchemy as sa
from alembic import op

revision = "0004_model_operations"
down_revision = "0003_task_budget_debit"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("project_model_operations"):
        op.create_table(
            "project_model_operations",
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
            sa.Column("owner", sa.String(36), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    op.drop_table("project_model_operations")
