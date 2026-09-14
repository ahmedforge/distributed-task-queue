import pytest
from httpx import ASGITransport, AsyncClient
from typing import AsyncGenerator

from app.main import app
from app.core.database import engine
from app.core.queue import queue


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