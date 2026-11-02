"""Admission budgets are serialized on the owning account, across API replicas."""

from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import case, or_
from sqlmodel import col, func, select
from app.core.config import settings
from app.models.account import Account
from app.models.job import Job, JobStatus


def unexpired():
    return or_(Job.expires_at == None, Job.expires_at > datetime.now(timezone.utc))


def expired(job):
    return job.expires_at is not None and job.expires_at <= datetime.now(timezone.utc)


async def lock_account(session, account):
    if account:
        await session.exec(
            select(Account.id).where(Account.id == account.id).with_for_update()
        )


async def usage(session, owner_id):
    # Tombstones and expired rows keep consuming budget until files are removed
    # and their database row is committed away. Never free space on a promise.
    reserved = func.greatest(Job.result_bytes, Job.result_reserved_bytes)
    result = (
        await session.exec(
            select(
                func.count(col(Job.id)),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Job.status.in_(
                                    [JobStatus.PENDING, JobStatus.PROCESSING]
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        func.coalesce(Job.input_bytes, 0)
                        + func.coalesce(Job.result_bytes, 0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        func.greatest(reserved - func.coalesce(Job.result_bytes, 0), 0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (or_(Job.input_bytes == None, Job.result_bytes == None), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(Job.owner_id == owner_id)
        )
    ).one()
    total, active, stored, pending, unknown = map(int, result)
    return {
        "retained_jobs": total,
        "active_jobs": active,
        "storage_bytes": stored,
        "reserved_bytes": pending,
        "storage_accounted": unknown == 0,
        "limits": {
            "active_jobs": settings.ACCOUNT_MAX_ACTIVE_JOBS,
            "retained_jobs": settings.ACCOUNT_MAX_STORED_JOBS,
            "storage_bytes": settings.ACCOUNT_MAX_STORAGE_BYTES,
            "max_result_bytes": settings.MAX_RESULT_BYTES,
            "retention_days": settings.JOB_RETENTION_DAYS,
        },
    }


async def admit(session, account, *, new_job=False, extra_bytes=0):
    if not account:  # Only existing unowned fixtures use the test dependency override.
        return
    current = await usage(session, account.id)
    if not current["storage_accounted"]:
        raise HTTPException(
            409,
            "Some existing files have not been measured. Ask the operator to run the storage accounting command, or delete old finished jobs.",
        )
    if current["active_jobs"] >= settings.ACCOUNT_MAX_ACTIVE_JOBS:
        raise HTTPException(
            429,
            "Your active job limit has been reached. Wait for a job to finish before submitting or retrying another.",
            headers={"Retry-After": "30"},
        )
    if new_job and current["retained_jobs"] >= settings.ACCOUNT_MAX_STORED_JOBS:
        raise HTTPException(
            409,
            "Your saved job limit has been reached. Delete finished jobs and wait for file cleanup before uploading again.",
        )
    if (
        current["storage_bytes"] + current["reserved_bytes"] + extra_bytes
        > settings.ACCOUNT_MAX_STORAGE_BYTES
    ):
        raise HTTPException(
            409,
            "Your storage budget cannot fit this job and its reserved result space. Delete finished jobs and wait for cleanup before trying again.",
        )
