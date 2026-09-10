import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.queue import queue
from app.models.job import Job
from app.schemas.job import JobCreate

router = APIRouter(prefix="/jobs", tags=["Jobs"])

@router.post("", response_model=Job, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_in: JobCreate,
    session: AsyncSession = Depends(get_session)
):
    # Idempotency check
    statement = select(Job).where(Job.idempotency_key == job_in.idempotency_key)
    result = await session.exec(statement)
    existing_job = result.first()
    
    if existing_job:
        return existing_job

    # Store row in Postgres
    db_job = Job.model_validate(job_in)
    session.add(db_job)
    await session.commit()
    await session.refresh(db_job)

    # Pass the generated Postgres UUID in the Redis queue payload
    await queue.enqueue(
        "process_pdf_render",
        payload={"id": str(db_job.id)},
        timeout=60
    )

    return db_job

@router.get("/{job_id}", response_model=Job)
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    return job