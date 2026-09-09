from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.database import get_async_session
from app.models.job import Job, JobStatus
from app.schemas.job import JobCreate

router = APIRouter(prefix="/jobs", tags=["Jobs"])

@router.post("", response_model=Job, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_in: JobCreate,
    session: AsyncSession = Depends(get_async_session),
):
    job = Job(
        idempotency_key=job_in.idempotency_key,
        job_type=job_in.job_type,
        payload=job_in.payload,
        status=JobStatus.PENDING,
    )
    
    session.add(job)
    try:
        await session.commit()
        await session.refresh(job)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job with idempotency key '{job_in.idempotency_key}' already exists.",
        )
        
    return job