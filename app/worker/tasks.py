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
    
    # Create an isolated AsyncSession for DB operations within worker thread
    async with AsyncSession(engine) as session:
        # 1. Fetch job record using UUID primary key
        statement = select(Job).where(Job.id == job_id)
        result = await session.exec(statement)
        job = result.first()
        
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            return {"status": "not_found"}

        # 2. Transition state to PROCESSING
        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()

        try:
            logger.info(f"==> [WORKER] Processing PDF render job #{job_id}...")
            
            # Simulate long-running I/O work
            await asyncio.sleep(3)

            # 3. Transition state to COMPLETED
            job.status = JobStatus.COMPLETED
            job.updated_at = datetime.utcnow()
            session.add(job)
            await session.commit()
            
            logger.info(f"==> [WORKER] Successfully finished PDF job #{job_id}")
            return {"status": "completed", "pdf_id": job_id}

        except Exception as exc:
            # 4. Handle exceptions and update job to FAILED
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.updated_at = datetime.utcnow()
            session.add(job)
            await session.commit()
            raise exc

SETTINGS = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": 5,
}