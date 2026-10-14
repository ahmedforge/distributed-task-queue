"""Durable delivery of committed jobs to SAQ; the Job row acts as an outbox."""

import asyncio
import logging
from sqlmodel import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.queue import queue
from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


async def dispatch_job(job_id, task_queue=None):
    task_queue = task_queue if task_queue is not None else queue
    async with async_session_maker() as session:
        job = (
            await session.exec(select(Job).where(Job.id == job_id).with_for_update())
        ).one_or_none()
        if not job or not job.dispatch_pending or job.status != JobStatus.PENDING:
            return True
        try:
            await asyncio.wait_for(
                task_queue.enqueue(
                    "process_pdf_render",
                    key=job.dispatch_key,
                    payload={"id": job.id, "dispatch_key": job.dispatch_key},
                    retries=job.max_retries,
                    retry_delay=settings.RETRY_BASE_DELAY,
                    retry_backoff=settings.RETRY_MAX_DELAY,
                    timeout=1800,
                ),
                timeout=3,
            )
        except Exception:
            # Leave the durable intent intact. API restarts resume delivery.
            logger.warning("Delivery deferred for job %s", job_id, exc_info=True)
            return False
        job.dispatch_pending = False
        session.add(job)
        await session.commit()
        return True


async def dispatch_pending():
    async with async_session_maker() as session:
        ids = (
            await session.exec(
                select(Job.id)
                .where(Job.dispatch_pending == True, Job.status == JobStatus.PENDING)
                .order_by(Job.id)
                .limit(100)
            )
        ).all()
    for job_id in ids:
        if not await dispatch_job(job_id):
            break  # Avoid waiting on every job during a Redis outage.


async def dispatch_loop():
    while True:
        try:
            await dispatch_pending()
        except Exception:
            logger.exception("Dispatch recovery pass failed")
        await asyncio.sleep(2)
