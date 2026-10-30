"""Persist credential generations so password rollback cannot resurrect sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0007_credential_generation"
down_revision = "0006_sessions_requirements"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "credential_generation" not in {
        c["name"] for c in sa.inspect(bind).get_columns("auth_sessions")
    }:
        op.add_column(
            "auth_sessions",
            sa.Column("credential_generation", sa.BigInteger(), nullable=False, server_default="0"),
        )
    if not sa.inspect(bind).has_table("auth_credentials"):
        op.create_table(
            "auth_credentials",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("salt", sa.String(64), nullable=False),
            sa.Column("verifier", sa.String(64), nullable=False),
            sa.Column("generation", sa.BigInteger(), nullable=False),
        )
    # Old cookies have no generation and must be replaced by a fresh login.
    bind.execute(sa.text("DELETE FROM auth_sessions"))


def downgrade():
    op.drop_table("auth_credentials")
    op.drop_column("auth_sessions", "credential_generation")
