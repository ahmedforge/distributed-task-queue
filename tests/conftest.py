import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel

from app.core.database import engine
from app.core.queue import queue
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def reset_test_environment():
    # 1. Setup DB Schema
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield

    # 2. Teardown DB Schema & Connection Pool
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()

    # 3. Teardown SAQ / Redis Connection Pool
    if hasattr(queue, "disconnect"):
        await queue.disconnect()
    elif hasattr(queue, "redis") and queue.redis:
        await queue.redis.aclose()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac