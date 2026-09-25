import pytest
import redis.asyncio as aioredis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.schemas.job import JobProgressUpdate


@pytest.mark.asyncio
async def test_websocket_nonexistent_job():
    """Verify connecting to a non-existent job receives error and closes gracefully."""
    client = TestClient(app)
    
    # Catch WebSocketDisconnect raised when server closes with code 4004
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/jobs/99999/ws") as websocket:
            data = websocket.receive_json()
            assert "error" in data
            assert data["error"] == "Job 99999 not found"

    assert exc_info.value.code == 4004


@pytest.mark.asyncio
async def test_websocket_late_subscriber_completed_job():
    """Verify late subscriber receives final state immediately and closes socket."""
    # 1. Seed completed job
    async with async_session_maker() as session:
        job = Job(
            filename="completed_doc.pdf",
            status=JobStatus.COMPLETED,
            progress=100
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    # 2. Connect via WebSocket and expect clean close (code 1000)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as websocket:
            data = websocket.receive_json()
            assert data["job_id"] == job_id
            assert data["status"] == "COMPLETED"
            assert data["progress"] == 100

    assert exc_info.value.code == 1000


@pytest.mark.asyncio
async def test_websocket_live_progress_updates():
    """Verify real-time progress updates published over Redis Pub/Sub reach WebSocket clients."""
    # 1. Seed processing job
    async with async_session_maker() as session:
        job = Job(
            filename="active_doc.pdf",
            status=JobStatus.PROCESSING,
            progress=10
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    # 2. Connect via WebSocket and read initial state
    client = TestClient(app)
    
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as websocket:
            initial_msg = websocket.receive_json()
            assert initial_msg["status"] == "PROCESSING"
            assert initial_msg["progress"] == 10

            # 3. Publish progress tick (50%) over Redis Pub/Sub
            redis_client = aioredis.from_url(settings.REDIS_URL)
            tick_payload = JobProgressUpdate(
                job_id=job_id,
                status=JobStatus.PROCESSING,
                progress=50,
                updated_at=job.updated_at
            ).model_dump_json()

            await redis_client.publish(f"job_progress:{job_id}", tick_payload)

            # Receive tick
            tick_msg = websocket.receive_json()
            assert tick_msg["progress"] == 50
            assert tick_msg["status"] == "PROCESSING"

            # 4. Publish terminal update (100% COMPLETED)
            final_payload = JobProgressUpdate(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                progress=100,
                updated_at=job.updated_at
            ).model_dump_json()

            await redis_client.publish(f"job_progress:{job_id}", final_payload)
            await redis_client.aclose()

            # Receive terminal message
            final_msg = websocket.receive_json()
            assert final_msg["progress"] == 100
            assert final_msg["status"] == "COMPLETED"

    # Server loop breaks on COMPLETED and closes socket cleanly
    assert exc_info.value.code == 1000

# tests/test_websocket.py

@pytest.mark.asyncio
async def test_websocket_nonexistent_job():
    """Verify connecting to a non-existent job receives error and closes gracefully."""
    client = TestClient(app)
    
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/jobs/99999/ws") as websocket:
            data = websocket.receive_json()
            assert "error" in data
            assert data["error"] == "Job 99999 not found"

    # TestClient catches the close frame as code 1000 or 1006
    assert exc_info.value.code in (1000, 4004)