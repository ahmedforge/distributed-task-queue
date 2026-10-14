"""Check migrations on a newly created disposable database, never an existing one."""

import asyncio
import re
import sys
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import async_session_maker, engine  # noqa: E402
from app.models.job import Job  # noqa: E402


async def create_disposable_database():
    name = settings.POSTGRES_DB
    if not re.fullmatch(r"[a-z_][a-z0-9_]*_migrations", name):
        raise RuntimeError(
            "POSTGRES_DB must name a disposable database ending in _migrations."
        )
    admin = await asyncpg.connect(
        host=settings.POSTGRES_SERVER,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database="postgres",
    )
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", name
        )
        if exists:
            raise RuntimeError(
                "Migration checks require a new database; this database already exists."
            )
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def verify_model_compatibility():
    engine.echo = False
    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns("job")
        )
        if {column["name"] for column in columns} != set(Job.__table__.columns.keys()):
            raise RuntimeError("Migration columns do not match the current Job model.")
    async with async_session_maker() as session:
        job = Job(filename="migration-check.pdf")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        assert job.id is not None and job.progress == 0
    await engine.dispose()


def main():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected one Alembic head, found {heads}")
    asyncio.run(create_disposable_database())
    command.upgrade(config, "head")
    asyncio.run(verify_model_compatibility())
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    asyncio.run(verify_model_compatibility())
    print(
        "Migration checks passed: fresh upgrade, model compatibility, full rollback, re-upgrade."
    )


if __name__ == "__main__":
    main()
