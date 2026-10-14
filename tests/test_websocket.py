import asyncio

import pytest
import redis.asyncio as aioredis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.worker import settings as worker_settings


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
            websocket.receive_json()  # Consume the close frame.

    assert exc_info.value.code == 4004


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "progress"), [(JobStatus.COMPLETED, 100), (JobStatus.FAILED, 10)]
)
async def test_websocket_late_subscriber_terminal_job(status, progress):
    """Verify late subscriber receives final state immediately and closes socket."""
    # 1. Seed completed job
    async with async_session_maker() as session:
        job = Job(filename="completed_doc.pdf", status=status, progress=progress)
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
            assert data["status"] == status.value
            assert data["progress"] == progress
            websocket.receive_json()  # Consume the close frame.

    assert exc_info.value.code == 1000


@pytest.mark.asyncio
async def test_websocket_live_progress_updates(store_pdf):
    """Verify real-time progress updates published over Redis Pub/Sub reach WebSocket clients."""
    # 1. Seed processing job
    async with async_session_maker() as session:
        job = Job(filename="active_doc.pdf", status=JobStatus.PROCESSING, progress=10)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    store_pdf(job_id)

    # 2. Connect via WebSocket and read initial state
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as websocket:
            initial_msg = websocket.receive_json()
            assert initial_msg["status"] == "PROCESSING"
            assert initial_msg["progress"] == 10

            # Wait for the endpoint to subscribe before running the real worker.
            async with aioredis.from_url(settings.REDIS_URL) as redis_client:
                async with asyncio.timeout(5):
                    while True:
                        subscribers = await redis_client.pubsub_numsub(
                            f"job_progress:{job_id}"
                        )
                        if subscribers[0][1] > 0:
                            break
                        await asyncio.sleep(0.01)

            await worker_settings["functions"][0](
                {}, payload={"id": job_id, "filename": job.filename}
            )

            # Verify worker checkpoints arrive through the WebSocket endpoint.
            for progress in [0, 10, 90, 100]:
                update = websocket.receive_json()
                assert update["job_id"] == job_id
                assert update["progress"] == progress
                assert update["status"] == (
                    "COMPLETED" if progress == 100 else "PROCESSING"
                )
            websocket.receive_json()  # Consume the terminal close frame.

    # Server loop breaks on COMPLETED and closes socket cleanly
    assert exc_info.value.code == 1000


@pytest.mark.asyncio
async def test_multiple_live_subscribers_receive_completion(store_pdf):
    async with async_session_maker() as session:
        job = Job(filename="subscribers.pdf")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    store_pdf(job_id)
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as first:
            with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as second:
                assert first.receive_json()["status"] == "PENDING"
                assert second.receive_json()["status"] == "PENDING"
                await worker_settings["functions"][0]({}, payload={"id": job_id})
                for socket in (first, second):
                    for progress in [0, 10, 90, 100]:
                        assert socket.receive_json()["progress"] == progress
                    with pytest.raises(WebSocketDisconnect) as exc:
                        socket.receive_json()
                    assert exc.value.code == 1000


@pytest.mark.asyncio
async def test_subscription_precedes_snapshot(monkeypatch):
    from redis.asyncio.client import PubSub
    from app.tasks import report_progress

    async with async_session_maker() as session:
        job = Job(filename="race.pdf")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    original_subscribe = PubSub.subscribe

    async def subscribe_and_complete(pubsub, *args, **kwargs):
        await original_subscribe(pubsub, *args, **kwargs)
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            await report_progress(session, job, 100, JobStatus.COMPLETED)

    monkeypatch.setattr(PubSub, "subscribe", subscribe_and_complete)
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as socket:
            initial = socket.receive_json()
            assert initial["status"] == "COMPLETED"
            assert initial["progress"] == 100
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


@pytest.mark.asyncio
async def test_idle_disconnect_releases_redis_subscription():
    async with async_session_maker() as session:
        job = Job(filename="idle.pdf")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/jobs/{job_id}/ws") as socket:
            assert socket.receive_json()["status"] == "PENDING"
        async with aioredis.from_url(settings.REDIS_URL) as redis_client:
            async with asyncio.timeout(5):
                while (await redis_client.pubsub_numsub(f"job_progress:{job_id}"))[0][
                    1
                ]:
                    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_redis_outage_closes_socket_for_client_fallback(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_PORT", 1)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/jobs/1/ws") as socket:
            with pytest.raises(WebSocketDisconnect) as exc:
                socket.receive_json()
            assert exc.value.code == 1013
