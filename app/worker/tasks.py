import asyncio
import logging
from datetime import datetime
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.core.queue import queue
from app.models.job import Job, JobStatus

logger = logging.getLogger("saq")

async def process_pdf_render(ctx, *, payload: dict) -> dict:
    job_id = payload.get("id")
    
    async with AsyncSession(engine) as session:
        # 1. Fetch Job and set status to PROCESSING
        statement = select(Job).where(Job.id == job_id)
        result = await session.exec(statement)
        job = result.first()
        
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            return {"status": "not_found"}

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()

        try:
            logger.info(f"==> [WORKER] Processing PDF render job #{job_id}...")
            
            # Simulate a deliberate failure if requested in payload for testing retries
            if payload.get("simulate_error"):
                raise ValueError("Simulated render engine crash")

            await asyncio.sleep(2)

            # 2. Mark as COMPLETED on success
            job.status = JobStatus.COMPLETED
            job.updated_at = datetime.utcnow()
            session.add(job)
            await session.commit()
            
            logger.info(f"==> [WORKER] Successfully finished PDF job #{job_id}")
            return {"status": "completed", "pdf_id": job_id}

        except Exception as exc:
            # 3. Handle failure, increment retry count, and update state
            job.retry_count += 1
            job.error_message = str(exc)
            job.updated_at = datetime.utcnow()

            if job.retry_count < job.max_retries:
                job.status = JobStatus.PENDING  # Reset for retry attempt
                logger.warning(
                    f"==> [WORKER] Job #{job_id} failed ({exc}). "
                    f"Retrying attempt {job.retry_count}/{job.max_retries}..."
                )
            else:
                job.status = JobStatus.FAILED  # Max retries exhausted
                logger.error(
                    f"==> [WORKER] Job #{job_id} failed permanently after {job.max_retries} retries."
                )

            session.add(job)
            await session.commit()
            raise exc

SETTINGS = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": 5,
}