import pytest
from app.core.database import engine

@pytest.fixture(autouse=True)
async def cleanup_db_engine():
    """Dispose of pooled connections after each test to prevent event loop mismatch."""
    yield
    await engine.dispose()
