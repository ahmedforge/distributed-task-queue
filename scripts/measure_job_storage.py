"""Measure finished legacy jobs; dry-run by default, no files are deleted."""

import argparse
import asyncio
import sys
from pathlib import Path
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import async_session_maker, engine
from app.core.storage import get_storage
from app.limits import lock_account
from app.models.account import Account
from app.models.job import Job, JobStatus


async def measure_finished_jobs(apply=False):
    async with async_session_maker() as session:
        ids = (
            await session.exec(
                select(Job.id).where(
                    Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED])
                )
            )
        ).all()
    count = 0
    for job_id in ids:
        async with async_session_maker() as session:
            owner = (
                await session.exec(select(Job.owner_id).where(Job.id == job_id))
            ).first()
            if owner is not None:
                account = await session.get(Account, owner)
                await lock_account(session, account)
            job = (
                await session.exec(
                    select(Job).where(Job.id == job_id).with_for_update()
                )
            ).one_or_none()
            if (
                not job
                or job.owner_id != owner
                or job.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
            ):
                continue
            storage = get_storage()
            source = await asyncio.to_thread(storage.size, job.id, "input.pdf")
            result = await asyncio.to_thread(storage.size, job.id, "pages.zip")
            if apply:
                job.input_bytes, job.result_bytes, job.result_reserved_bytes = (
                    source,
                    result,
                    0,
                )
                session.add(job)
                await session.commit()
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Save measured sizes; omit to inspect without changing metadata.",
    )
    args = parser.parse_args()

    async def run():
        try:
            count = await measure_finished_jobs(args.apply)
            print(
                f"{'Updated' if args.apply else 'Inspected'} {count} finished jobs. Active jobs skipped; no files deleted."
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
