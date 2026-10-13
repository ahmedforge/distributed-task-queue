import asyncio
from uuid import uuid4

import pytest
from saq import Queue
from saq.job import Status
from saq.worker import Worker

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.worker import settings as worker_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_limit", [1, 3])
async def test_real_queue_retry_exhaustion_and_manual_requeue(
    client, monkeypatch, attempt_limit
):
    test_queue = Queue.from_url(settings.REDIS_URL, name=f"retry-test-{uuid4().hex}")
    monkeypatch.setattr("app.api.v1.jobs.queue", test_queue)
    worker = Worker(test_queue, functions=worker_settings["functions"], concurrency=1)
    try:
        response = await client.post(
            "/api/v1/jobs/upload",
            files={
                "file": ("corrupted.pdf", b"%PDF-1.4\nbroken", "application/pdf"),
            },
            data={"max_retries": str(attempt_limit)},
        )
        assert response.status_code == 201
        job_id = response.json()["id"]

        async def exhaust_attempts():
            for attempt in range(1, attempt_limit + 1):
                async with asyncio.timeout(5):
                    assert await worker.process()
                async with async_session_maker() as session:
                    job = await session.get(Job, job_id)
                    assert job.retry_count == attempt
                    assert job.status == (
                        JobStatus.FAILED
                        if attempt == attempt_limit
                        else JobStatus.PENDING
                    )

        await exhaust_attempts()
        dlq = (await client.get("/api/v1/jobs/dlq")).json()
        assert any(job["id"] == job_id for job in dlq)

        # SAQ must also have exhausted the same attempt budget.
        queue_jobs = await test_queue.iter_jobs([Status.FAILED]).__anext__()
        assert queue_jobs.attempts == attempt_limit
        assert queue_jobs.retries == attempt_limit

        response = await client.post(f"/api/v1/jobs/{job_id}/requeue")
        assert response.status_code == 200
        assert response.json()["retry_count"] == 0
        assert response.json()["progress"] == 0
        assert response.json()["status"] == "PENDING"
        await exhaust_attempts()
    finally:
        worker.pool.shutdown(wait=True)
        await test_queue.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_limit", ["0", "-1", "invalid"])
async def test_invalid_retry_limits_are_rejected(client, invalid_limit, pdf_bytes):
    response = await client.post(
        "/api/v1/jobs/upload",
        files={
            "file": ("test.pdf", pdf_bytes, "application/pdf"),
        },
        data={"max_retries": invalid_limit},
    )
    assert response.status_code == 422
