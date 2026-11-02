"""Track artifact budgets and opt new jobs into expiry; preserve legacy files."""

from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("input_bytes", "result_bytes"):
        op.add_column("job", sa.Column(name, sa.BigInteger(), nullable=True))
    op.add_column(
        "job",
        sa.Column(
            "result_reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "job",
        sa.Column(
            "result_limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="104857600",
        ),
    )
    op.add_column("job", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.add_column(
        "job", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_job_expires_at", "job", ["expires_at"])


def downgrade():
    op.drop_index("ix_job_expires_at", table_name="job")
    for name in (
        "expires_at",
        "retention_days",
        "result_limit_bytes",
        "result_reserved_bytes",
        "result_bytes",
        "input_bytes",
    ):
        op.drop_column("job", name)
