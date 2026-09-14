from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.queue import queue
from app.models.job import Job, JobCreate, JobRead, JobStatus

router = APIRouter()


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=JobRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_job(job_in: JobCreate, session: AsyncSession = Depends(get_session)):
    if job_in.idempotency_key:
        statement = select(Job).where(Job.idempotency_key == job_in.idempotency_key)
        existing = (await session.exec(statement)).first()
        if existing:
            return existing

    job = Job.model_validate(job_in)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    await queue.enqueue(
        "process_pdf_render",
        payload={"id": job.id, "filename": job.filename}
    )
    return job


@router.get("/dlq", response_model=List[JobRead])
async def list_dlq_jobs(session: AsyncSession = Depends(get_session)):
    statement = select(Job).where(Job.status == JobStatus.FAILED)
    result = await session.exec(statement)
    return result.all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/requeue", response_model=JobRead)
async def requeue_failed_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Only FAILED jobs can be requeued. Current status: {job.status}"
        )

    job.status = JobStatus.PENDING
    job.retry_count = 0
    job.error_message = None
    job.updated_at = datetime.now(timezone.utc)

    session.add(job)
    await session.commit()
    await session.refresh(job)

    await queue.enqueue(
        "process_pdf_render",
        payload={"id": job.id, "filename": job.filename}
    )
    return job