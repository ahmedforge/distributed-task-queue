from datetime import datetime
from typing import Dict
from pydantic import BaseModel
from app.models.job import JobStatus


class JobProgressUpdate(BaseModel):
    job_id: int
    status: JobStatus
    progress: int
    updated_at: datetime


class JobStatsResponse(BaseModel):
    queue_name: str = "default"
    total_jobs: int
    status_counts: Dict[str, int]
    dlq_count: int