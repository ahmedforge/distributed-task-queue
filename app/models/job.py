from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import DateTime, Field, SQLModel


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    status: JobStatus = Field(default=JobStatus.PENDING)

    progress: int = Field(default=0, nullable=False)

    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    idempotency_key: Optional[str] = Field(default=None, unique=True)
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
    max_retries: Optional[int] = 3
    idempotency_key: Optional[str] = None


class JobRead(SQLModel):
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