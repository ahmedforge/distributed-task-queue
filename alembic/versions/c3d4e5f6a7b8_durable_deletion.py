"""Persist user-requested deletion until storage cleanup completes."""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "job",
        sa.Column(
            "delete_pending", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_job_delete_pending", "job", ["delete_pending"])


def downgrade():
    op.drop_index("ix_job_delete_pending", table_name="job")
    op.drop_column("job", "delete_pending")
