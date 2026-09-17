from typing import Any, Dict
from pydantic import BaseModel, Field

class JobCreate(BaseModel):
    idempotency_key: str = Field(..., min_length=1, description="Unique key preventing duplicate submissions")
    job_type: str = Field(..., min_length=1, json_schema_extra={"example": "pdf_processing"})
    payload: Dict[str, Any] = Field(default_factory=dict)

class JobStatsResponse(BaseModel):
    total_jobs: int
    status_counts: Dict[str, int]
    dlq_count: int
    queue_name: str