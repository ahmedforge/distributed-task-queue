from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import DateTime, Field, SQLModel
from sqlalchemy import Index, UniqueConstraint, text, BigInteger


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Job(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_job_owner_idempotency"
        ),
        Index(
            "uq_job_legacy_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where=text("owner_id IS NULL"),
        ),
    )
    owner_id: Optional[int] = Field(default=None, foreign_key="account.id", index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    input_bytes: Optional[int] = Field(default=None, sa_type=BigInteger)
    result_bytes: Optional[int] = Field(default=None, sa_type=BigInteger)
    result_reserved_bytes: int = Field(default=0, sa_type=BigInteger)
    result_limit_bytes: int = Field(default=100 * 1024 * 1024, sa_type=BigInteger)
    retention_days: Optional[int] = Field(default=None)
    expires_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True), index=True
    )
    filename: str
    status: JobStatus = Field(default=JobStatus.PENDING)

    progress: int = Field(default=0, nullable=False)
    dispatch_key: Optional[str] = Field(default=None)
    dispatch_pending: bool = Field(default=False, nullable=False, index=True)
    delete_pending: bool = Field(default=False, nullable=False, index=True)

    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    idempotency_key: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
    )


class JobCreate(SQLModel):
    filename: str
    max_retries: int = Field(
        default=3,
        ge=1,
        description="Maximum total processing attempts, including the first attempt.",
    )
    idempotency_key: Optional[str] = None


class JobRead(SQLModel):
    expires_at: Optional[datetime] = None
    id: int
    filename: str
    status: JobStatus
    progress: int
    retry_count: int
    max_retries: int
    idempotency_key: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
