"""Durable deletion: remove files before dropping the tombstoned Job row."""

import asyncio
import logging
import shutil
from sqlmodel import select

from app.core.database import async_session_maker
from app.core.storage import job_directory
from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


def remove_job_files(job_id):
    directory = job_directory(job_id)
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        if directory.exists():
            raise  # Partial cleanup needs another pass.
        # Missing directories are already cleaned up.


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
