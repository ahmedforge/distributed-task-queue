import pytest
from httpx import ASGITransport, AsyncClient
from typing import AsyncGenerator

from app.main import app
from app.core.database import engine
from app.core.queue import queue
import pytest_asyncio
from sqlmodel import SQLModel
from app.core.database import engine
import app.models.job  # Ensure models are registered

@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an AsyncClient for test functions."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
async def cleanup_resources():
    """Clean up connection pools between tests."""
    yield
    # Dispose SQLAlchemy connections
    await engine.dispose()
    # Disconnect SAQ Redis pool
    if hasattr(queue, "disconnect"):
        await queue.disconnect()
@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield