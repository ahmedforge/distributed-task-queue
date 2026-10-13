import logging
from datetime import datetime, timezone
from typing import Any, Dict

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.schemas.job import JobProgressUpdate
from app.pdf import render_pdf

logger = logging.getLogger("saq")


async def report_progress(session, job: Job, progress: int, status: JobStatus):
    """Persist a checkpoint before notifying live subscribers."""
    job.progress = progress
    job.status = status
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    await session.commit()

    update = JobProgressUpdate(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        updated_at=job.updated_at,
    )
    try:
        async with aioredis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        ) as redis_client:
            await redis_client.publish(
                f"job_progress:{job.id}", update.model_dump_json()
            )
    except Exception:
        # A notification outage must not change the persisted task outcome.
        logger.warning("Could not publish progress for job %s", job.id, exc_info=True)


async def process_pdf_render(ctx: Dict[str, Any], **kwargs: Any):
    """Background task function to process PDF rendering.
    
    Handles both direct kwargs (id=1, filename='foo.pdf') 
    and dictionary wrapped kwargs (payload={'id': 1, 'filename': 'foo.pdf'}).
    """
    # Extract payload regardless of how SAQ unpacked kwargs
    payload = kwargs.get("payload", kwargs)
    job_id = payload.get("id")

    if not job_id:
        logger.error(f"Received job payload missing 'id': {kwargs}")
        return

    async with async_session_maker() as session:
        # 1. Fetch job and update status to PROCESSING
        job = await session.get(Job, job_id)
        if not job:
            logger.warning(f"Job ID {job_id} not found in database.")
            return

        await report_progress(session, job, 0, JobStatus.PROCESSING)

        try:
            async def on_progress(progress: int):
                await report_progress(session, job, progress, JobStatus.PROCESSING)

            await render_pdf(job_id, on_progress)

            # 3. Mark job as COMPLETED
            job.error_message = None
            await report_progress(session, job, 100, JobStatus.COMPLETED)
            logger.info(f"Successfully processed Job ID {job_id}")

        except Exception as exc:
            # 4. Handle failure and update database state
            job.retry_count += 1
            job.status = (
                JobStatus.FAILED
                if job.retry_count >= job.max_retries
                else JobStatus.PENDING
            )

            job.error_message = str(exc)
            await report_progress(session, job, job.progress, job.status)
            logger.error(f"Failed processing Job ID {job_id}: {exc}")
            raise exc