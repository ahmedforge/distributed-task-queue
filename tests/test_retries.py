import asyncio
import time
from uuid import uuid4

import pytest
from saq import Queue
from saq.job import Job as QueueJob, Status
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
    monkeypatch.setattr(settings, "RETRY_BASE_DELAY", 0.1)
    monkeypatch.setattr(settings, "RETRY_MAX_DELAY", 0.2)
    monkeypatch.setattr("saq.utils.random", lambda: 1.0)
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
                if attempt > 1:
                    # The real worker's scheduler moves due delayed jobs to the ready queue.
                    async with asyncio.timeout(5):
                        while not await test_queue.schedule():
                            await asyncio.sleep(0.05)
                started = time.time()
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

                if attempt < attempt_limit:
                    queued = await test_queue.iter_jobs([Status.QUEUED]).__anext__()
                    assert queued.retry_delay == settings.RETRY_BASE_DELAY
                    assert queued.retry_backoff == settings.RETRY_MAX_DELAY
                    assert queued.attempts == attempt
                    scheduled = await test_queue.redis.zscore(
                        test_queue._incomplete, queued.id
                    )
                    assert scheduled >= started + min(0.2, 0.1 * 2 ** (attempt - 1))
                    assert await test_queue.redis.llen(test_queue._queued) == 0

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


@pytest.mark.parametrize(("attempt", "expected"), [(1, 2), (2, 4), (3, 8), (7, 60)])
def test_backoff_growth_and_cap(monkeypatch, attempt, expected):
    monkeypatch.setattr("saq.utils.random", lambda: 1.0)
    job = QueueJob(
        function="process_pdf_render", attempts=attempt, retry_delay=2, retry_backoff=60
    )
    assert job.next_retry_delay() == expected


def test_backoff_applies_jitter(monkeypatch):
    monkeypatch.setattr("saq.utils.random", lambda: 0.5)
    job = QueueJob(
        function="process_pdf_render", attempts=2, retry_delay=2, retry_backoff=60
    )
    assert job.next_retry_delay() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_started", [False, True])
async def test_requeue_enqueue_failure_is_recoverable_without_overwriting_worker(
    client, monkeypatch, worker_started
):
    async with async_session_maker() as session:
        job = Job(
            filename="failed.pdf",
            status=JobStatus.FAILED,
            retry_count=3,
            max_retries=3,
            progress=40,
            error_message="Previous rendering failure",
            idempotency_key="retry-key",
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    async def unavailable(*args, **kwargs):
        if worker_started:
            async with async_session_maker() as session:
                current = await session.get(Job, job_id)
                current.status = JobStatus.PROCESSING
                current.progress = 10
                await session.commit()
        raise ConnectionError("Redis connection lost")

    monkeypatch.setattr("app.api.v1.jobs.queue.enqueue", unavailable)
    response = await client.post(f"/api/v1/jobs/{job_id}/requeue")
    assert response.status_code == 503
    assert response.json()["detail"]["job_id"] == job_id
    current = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert current["idempotency_key"] == "retry-key"
    if worker_started:
        assert current["status"] == "PROCESSING"
        assert current["progress"] == 10
    else:
        assert current["status"] == "FAILED"
        assert current["retry_count"] == 3
        assert current["progress"] == 40
        assert "Queue unavailable" in current["error_message"]
        assert any(
            j["id"] == job_id for j in (await client.get("/api/v1/jobs/dlq")).json()
        )

        async def available(*args, **kwargs):
            return object()

        monkeypatch.setattr("app.api.v1.jobs.queue.enqueue", available)
        retried = await client.post(f"/api/v1/jobs/{job_id}/requeue")
        assert retried.status_code == 200
        assert retried.json()["status"] == "PENDING"
        assert retried.json()["retry_count"] == 0


@pytest.mark.asyncio
async def test_concurrent_requeues_enqueue_only_once(client, monkeypatch):
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with async_session_maker() as session:
        job = Job(filename="failed.pdf", status=JobStatus.FAILED, retry_count=3)
        session.add(job)
        await session.commit()
        job_id = job.id

    original_get = AsyncSession.get
    both_read = asyncio.Event()
    reads = 0
    enqueues = []

    async def synchronized_get(self, model, ident, **kwargs):
        nonlocal reads
        result = await original_get(self, model, ident, **kwargs)
        if model is Job and ident == job_id:
            reads += 1
            if reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=5)
        return result

    async def enqueue(*args, **kwargs):
        enqueues.append(kwargs["payload"]["id"])
        return object()

    # The old get/check/update path deterministically reads FAILED twice.
    # The locked SELECT path serializes the claims instead.
    monkeypatch.setattr(AsyncSession, "get", synchronized_get)
    monkeypatch.setattr("app.api.v1.jobs.queue.enqueue", enqueue)
    responses = await asyncio.gather(
        client.post(f"/api/v1/jobs/{job_id}/requeue"),
        client.post(f"/api/v1/jobs/{job_id}/requeue"),
    )
    assert sorted(r.status_code for r in responses) == [200, 400]
    assert enqueues == [job_id]
