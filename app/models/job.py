from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import Column, DateTime, Field, SQLModel


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobBase(SQLModel):
    filename: str
    max_retries: int = 3
    idempotency_key: Optional[str] = Field(default=None, index=True)


class Job(JobBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    status: JobStatus = Field(default=JobStatus.PENDING)
    retry_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class JobRead(JobBase):
    id: int
    status: JobStatus
    retry_count: int
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime


class JobCreate(JobBase):
    pass