"""Add accounts and owner-scoped idempotency; preserve unowned legacy jobs."""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
    )
    op.create_index("ix_account_username", "account", ["username"], unique=True)
    op.create_table(
        "accountsession",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_accountsession_account_id", "accountsession", ["account_id"])
    op.create_index("ix_accountsession_expires_at", "accountsession", ["expires_at"])
    op.add_column(
        "job",
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("account.id", name="fk_job_owner_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_job_owner_id", "job", ["owner_id"])
    op.drop_constraint("job_idempotency_key_key", "job", type_="unique")
    op.create_unique_constraint(
        "uq_job_owner_idempotency", "job", ["owner_id", "idempotency_key"]
    )
    op.create_index(
        "uq_job_legacy_idempotency",
        "job",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )


def downgrade():
    # Refuse a lossy rollback when owners have reused the same key.
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM job WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates:
        raise RuntimeError(
            "Cannot restore global idempotency: duplicate keys exist across accounts. Back up and resolve them explicitly before rollback."
        )
    op.drop_index("uq_job_legacy_idempotency", table_name="job")
    op.drop_constraint("uq_job_owner_idempotency", "job", type_="unique")
    op.create_unique_constraint("job_idempotency_key_key", "job", ["idempotency_key"])
    op.drop_index("ix_job_owner_id", table_name="job")
    op.drop_constraint("fk_job_owner_id", "job", type_="foreignkey")
    op.drop_column("job", "owner_id")
    op.drop_table("accountsession")
    op.drop_table("account")
