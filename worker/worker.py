import asyncio
from datetime import datetime, timezone
import redis.asyncio as aioredis
from sqlmodel import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.schemas.job import JobProgressUpdate

async def report_progress(job_id: int, progress: int, status: JobStatus, error_message: str = None):
    """Update job state in PostgreSQL and publish progress to Redis Pub/Sub."""
    now = datetime.now(timezone.utc)
    
    # 1. Update PostgreSQL
    async with async_session_maker() as session:
        statement = select(Job).where(Job.id == job_id)
        result = await session.exec(statement)
        job = result.one()
        
        job.progress = progress
        job.status = status
        job.updated_at = now
        if error_message:
            job.error_message = error_message
            
        session.add(job)
        await session.commit()

    # 2. Publish to Redis channel
    redis_client = aioredis.from_url(settings.REDIS_URL)
    payload = JobProgressUpdate(
        job_id=job_id,
        status=status,
        progress=progress,
        updated_at=now
    ).model_dump_json()
    
    await redis_client.publish(f"job_progress:{job_id}", payload)
    await redis_client.aclose()


async def process_pdf_render(ctx, job_id: int):
    """Task execution with progress checkpoints."""
    try:
        # Checkpoint 1: Ingestion
        await report_progress(job_id, progress=10, status=JobStatus.PROCESSING)
        await asyncio.sleep(1)

        # Checkpoint 2: Extracting vectors
        await report_progress(job_id, progress=40, status=JobStatus.PROCESSING)
        await asyncio.sleep(1)

        # Checkpoint 3: Rasterizing pages
        await report_progress(job_id, progress=75, status=JobStatus.PROCESSING)
        await asyncio.sleep(1)

        # Checkpoint 4: Completion
        await report_progress(job_id, progress=100, status=JobStatus.COMPLETED)

    except Exception as e:
        await report_progress(
            job_id, 
            progress=100, 
            status=JobStatus.FAILED, 
            error_message=str(e)
        )
        raise e