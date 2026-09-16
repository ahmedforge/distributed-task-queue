import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.database import async_session_maker
from app.models.job import Job, JobStatus

logger = logging.getLogger("saq")


async def process_pdf_render(ctx: Dict[str, Any], **kwargs: Any):
    """Background task function to process PDF rendering.
    
    Handles both direct kwargs (id=1, filename='foo.pdf') 
    and dictionary wrapped kwargs (payload={'id': 1, 'filename': 'foo.pdf'}).
    """
    # Extract payload regardless of how SAQ unpacked kwargs
    payload = kwargs.get("payload", kwargs)
    job_id = payload.get("id")
    filename = payload.get("filename", "")

    if not job_id:
        logger.error(f"Received job payload missing 'id': {kwargs}")
        return

    async with async_session_maker() as session:
        # 1. Fetch job and update status to PROCESSING
        job = await session.get(Job, job_id)
        if not job:
            logger.warning(f"Job ID {job_id} not found in database.")
            return

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        session.add(job)
        await session.commit()

        try:
            # 2. Simulate execution (trigger failure for test file)
            if filename == "corrupted.pdf":
                raise ValueError("Corrupted PDF file cannot be processed.")

            await asyncio.sleep(2)

            # 3. Mark job as COMPLETED
            job.status = JobStatus.COMPLETED
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            await session.commit()
            logger.info(f"Successfully processed Job ID {job_id}")

        except Exception as exc:
            # 4. Handle failure and update database state
            job.retry_count += 1
            if job.retry_count >= job.max_retries:
                job.status = JobStatus.FAILED

            job.error_message = str(exc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            await session.commit()
            logger.error(f"Failed processing Job ID {job_id}: {exc}")
            raise exc