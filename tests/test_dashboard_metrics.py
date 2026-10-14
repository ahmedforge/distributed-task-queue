from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from saq import Queue

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus


@pytest.mark.asyncio
async def test_queue_metrics_count_ready_active_and_delayed_jobs(client, monkeypatch):
    queue = Queue.from_url(settings.REDIS_URL, name=f"metrics-test-{uuid4().hex}")
    monkeypatch.setattr("app.api.v1.jobs.queue", queue)
    try:
        await queue.enqueue("process_pdf_render", payload={"id": 1})
        await queue.enqueue("process_pdf_render", payload={"id": 2})
        await queue.enqueue(
            "process_pdf_render",
            payload={"id": 3},
            scheduled=int(datetime.now(timezone.utc).timestamp()) + 120,
        )
        await queue.dequeue(timeout=1)
        response = await client.get("/api/v1/jobs/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["queue_counts"] == {"queued": 1, "active": 1, "scheduled": 1}
        assert data["queue_depth"] == 2
        assert data["queue_error"] is None
        # Queue depth is broker data, independent of database row counts.
        assert data["total_jobs"] == 0
    finally:
        await queue.disconnect()


@pytest.mark.asyncio
async def test_completion_metrics_are_grouped_and_zero_filled(client, monkeypatch):
    import app.api.v1.jobs as routes

    fixed = datetime(2026, 10, 14, 5, 20, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(routes, "datetime", Clock)
    async with async_session_maker() as session:
        for minutes in [1, 1, 14, 20]:
            session.add(
                Job(
                    filename="done.pdf",
                    status=JobStatus.COMPLETED,
                    updated_at=fixed - timedelta(minutes=minutes),
                )
            )
        session.add(
            Job(filename="failed.pdf", status=JobStatus.FAILED, updated_at=fixed)
        )
        await session.commit()
    data = (await client.get("/api/v1/jobs/stats")).json()
    points = data["completed_per_minute"]
    assert len(points) == 15
    assert sum(point["completed"] for point in points) == 3
    assert points[0]["completed"] == 1
    assert points[-2]["completed"] == 2
    assert points[-1]["completed"] == 0
    assert all(
        points[index]["minute"] < points[index + 1]["minute"] for index in range(14)
    )


@pytest.mark.asyncio
async def test_redis_outage_does_not_turn_missing_metrics_into_zero(
    client, monkeypatch
):
    queue = Queue.from_url("redis://127.0.0.1:1", name="unavailable")
    monkeypatch.setattr("app.api.v1.jobs.queue", queue)
    try:
        response = await client.get("/api/v1/jobs/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["queue_counts"] is None
        assert data["queue_depth"] is None
        assert data["queue_error"]
        assert data["total_jobs"] == 0
        assert len(data["completed_per_minute"]) == 15
        assert all(point["completed"] == 0 for point in data["completed_per_minute"])
    finally:
        await queue.redis.aclose()
