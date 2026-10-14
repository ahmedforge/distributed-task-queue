from datetime import datetime
from typing import Dict
from pydantic import BaseModel
from app.models.job import JobRead, JobStatus


class JobProgressUpdate(BaseModel):
    job_id: int
    status: JobStatus
    progress: int
    updated_at: datetime


class ThroughputPoint(BaseModel):
    minute: datetime
    completed: int


class JobStatsResponse(BaseModel):
    queue_name: str = "default"
    total_jobs: int
    status_counts: Dict[str, int]
    dlq_count: int
    sampled_at: datetime
    queue_counts: Dict[str, int] | None
    queue_depth: int | None
    queue_error: str | None
    completed_per_minute: list[ThroughputPoint]


class JobListResponse(BaseModel):
    items: list[JobRead]
    total: int
    page: int
    page_size: int
