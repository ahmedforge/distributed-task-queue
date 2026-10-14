"""Persist database-to-queue delivery intent."""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job", sa.Column("dispatch_key", sa.String(), nullable=True))
    op.add_column(
        "job",
        sa.Column(
            "dispatch_pending", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_job_dispatch_pending", "job", ["dispatch_pending"])


def downgrade():
    op.drop_index("ix_job_dispatch_pending", table_name="job")
    op.drop_column("job", "dispatch_pending")
    op.drop_column("job", "dispatch_key")
