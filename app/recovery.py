"""Reconcile interrupted rendering attempts with SAQ's persisted outcome."""

import asyncio

import logging
from saq.job import Status
from sqlmodel import select

from app.core.database import async_session_maker
from app.core.queue import queue
from app.models.job import Job, JobStatus
from app.tasks import report_progress
from app.observability import record_terminal_failure

logger = logging.getLogger(__name__)


async def reconcile_attempt(job_id, queued):
    if queued is None:
        return  # Missing Redis data is not evidence of a rendering failure.
    async with async_session_maker() as session:
        job = (
            await session.exec(select(Job).where(Job.id == job_id).with_for_update())
        ).one_or_none()
        if not job or job.status != JobStatus.PROCESSING:
            return
        if queued.queue is not None:
            # Refresh under the database lock: another attempt may have started
            # between the initial broker snapshot and locking the Job row.
            queued = await asyncio.wait_for(queued.queue.job(queued.key), timeout=2)
            if queued is None:
                return
        payload = (queued.kwargs or {}).get("payload", queued.kwargs or {})
        if job.dispatch_key and payload.get("dispatch_key") != job.dispatch_key:
            return
        if queued.status == Status.QUEUED:
            target = (
                JobStatus.FAILED
                if queued.attempts >= job.max_retries
                else JobStatus.PENDING
            )
            reason = "Processing was interrupted; retry scheduled."
        elif queued.status in (Status.FAILED, Status.ABORTED):
            target = JobStatus.FAILED
            reason = "Processing was interrupted or timed out; queue attempt budget exhausted."
        else:
            return
        job.retry_count = max(job.retry_count, queued.attempts)
        job.error_message = (
            reason
            if target == JobStatus.PENDING
            else "Processing was interrupted or timed out; manual requeue is required."
        )
        await report_progress(session, job, job.progress, target)
        if target == JobStatus.FAILED:
            await record_terminal_failure()


async def after_process(ctx):
    queued = ctx.get("job")
    if not queued or queued.function != "process_pdf_render":
        return
    payload = (queued.kwargs or {}).get("payload", queued.kwargs or {})
    if payload.get("id"):
        await reconcile_attempt(payload["id"], queued)


async def reconcile_stalled_jobs():
    async with async_session_maker() as session:
        rows = (
            await session.exec(
                select(Job.id, Job.dispatch_key)
                .where(Job.status == JobStatus.PROCESSING, Job.dispatch_key != None)
                .order_by(Job.updated_at)
                .limit(100)
            )
        ).all()
    for job_id, key in rows:
        await reconcile_attempt(
            job_id, await asyncio.wait_for(queue.job(key), timeout=2)
        )
