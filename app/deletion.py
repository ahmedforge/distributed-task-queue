"""Durable deletion: remove files before dropping the tombstoned Job row."""

import asyncio
import logging
from datetime import datetime, timezone
from sqlmodel import select

from app.core.database import async_session_maker
from app.core.storage import get_storage
from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


def remove_job_files(job_id):
    get_storage().delete_job(job_id)


async def cleanup_job(job_id):
    async with async_session_maker() as session:
        job = (
            await session.exec(select(Job).where(Job.id == job_id).with_for_update())
        ).one_or_none()
        if not job:
            return True
        if not job.delete_pending or job.status not in (
            JobStatus.COMPLETED,
            JobStatus.FAILED,
        ):
            return False
        try:
            await asyncio.to_thread(remove_job_files, job_id)
            await session.delete(job)
            await session.commit()
        except Exception:
            # The committed tombstone survives partial cleanup and API crashes.
            logger.warning(
                "Deletion cleanup deferred for job %s", job_id, exc_info=True
            )
            return False
        return True


async def cleanup_pending_deletions():
    async with async_session_maker() as session:
        ids = (
            await session.exec(
                select(Job.id)
                .where(Job.delete_pending == True)
                .order_by(Job.id)
                .limit(100)
            )
        ).all()
    for job_id in ids:
        await cleanup_job(job_id)


async def expire_finished_jobs():
    async with async_session_maker() as session:
        jobs = (
            await session.exec(
                select(Job)
                .where(
                    Job.expires_at != None,
                    Job.expires_at <= datetime.now(timezone.utc),
                    Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                    Job.delete_pending == False,
                )
                .order_by(Job.expires_at, Job.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for job in jobs:
            job.delete_pending = True
            job.dispatch_pending = False
            session.add(job)
        await session.commit()
        return len(jobs)
