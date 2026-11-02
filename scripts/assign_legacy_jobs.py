"""Explicit operator-only assignment of unowned pre-account jobs; dry-run by default."""

import argparse
import asyncio
import sys
from pathlib import Path
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import async_session_maker, engine
from app.models.account import Account
from app.models.job import Job
from app.limits import lock_account


async def assign(username, apply=False):
    async with async_session_maker() as session:
        account = (
            await session.exec(
                select(Account).where(Account.username == username.strip().lower())
            )
        ).one_or_none()
        if not account:
            raise ValueError(
                "Account not found. Create the account through the app first."
            )
        await lock_account(session, account)
        jobs = (
            await session.exec(
                select(Job).where(Job.owner_id == None).with_for_update()
            )
        ).all()
        keys = {job.idempotency_key for job in jobs if job.idempotency_key}
        owned = (
            await session.exec(select(Job).where(Job.owner_id == account.id))
        ).all()
        if any(job.idempotency_key in keys for job in owned if job.idempotency_key):
            raise ValueError(
                "An existing job for this account uses a legacy idempotency key. Resolve that conflict explicitly before assignment."
            )
        count = len(jobs)
        if apply:
            for job in jobs:
                job.owner_id = account.id
                session.add(job)
            await session.commit()
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Assign all unowned jobs to this account. Omit for a dry-run.",
    )
    args = parser.parse_args()

    async def run():
        try:
            count = await assign(args.username, args.apply)
            print(
                f"{'Assigned' if args.apply else 'Would assign'} {count} unowned jobs to {args.username.strip().lower()}."
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
