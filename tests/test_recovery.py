import asyncio
from uuid import uuid4

import pytest
from saq import Queue
from saq.job import Job as QueueJob, Status
from saq.worker import Worker
from saq.utils import now

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.recovery import reconcile_attempt, reconcile_stalled_jobs
from app.tasks import process_pdf_render
from app.worker import settings as worker_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_limit", [1, 2])
async def test_real_worker_timeout_updates_database_and_exhausts_budget(
    monkeypatch, attempt_limit
):
    key = uuid4().hex
    queue = Queue.from_url(settings.REDIS_URL, name=f"timeout-{key}")
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key=key, max_retries=attempt_limit)
        session.add(job)
        await session.commit()
        job_id = job.id

    async def hangs(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.tasks.render_pdf", hangs)
    await queue.enqueue(
        "process_pdf_render",
        key=key,
        payload={"id": job_id, "dispatch_key": key},
        timeout=0.3,
        retries=attempt_limit,
        retry_delay=0.01,
    )
    worker = Worker(
        queue,
        functions=worker_settings["functions"],
        after_process=worker_settings["after_process"],
        concurrency=1,
    )
    try:
        for attempt in range(1, attempt_limit + 1):
            if attempt > 1:
                async with asyncio.timeout(5):
                    while not await queue.schedule():
                        await asyncio.sleep(0.05)
            async with asyncio.timeout(5):
                assert await worker.process()
            async with async_session_maker() as session:
                job = await session.get(Job, job_id)
                assert job.retry_count == attempt
                assert job.status == (
                    JobStatus.FAILED if attempt == attempt_limit else JobStatus.PENDING
                )
                assert "interrupted" in job.error_message
        assert (await queue.job(key)).status == Status.FAILED
    finally:
        worker.pool.shutdown(wait=True)
        await queue.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_limit", [1, 2])
async def test_swept_dead_worker_is_reconciled(monkeypatch, attempt_limit):
    key = uuid4().hex
    queue = Queue.from_url(settings.REDIS_URL, name=f"sweep-{key}")
    monkeypatch.setattr("app.recovery.queue", queue)
    async with async_session_maker() as session:
        job = Job(
            filename="test.pdf",
            dispatch_key=key,
            status=JobStatus.PROCESSING,
            max_retries=attempt_limit,
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    try:
        await queue.enqueue(
            "process_pdf_render",
            key=key,
            payload={"id": job_id, "dispatch_key": key},
            timeout=1,
            retries=attempt_limit,
        )
        queued = await queue.dequeue(timeout=1)
        queued.attempts = 1
        queued.started = now() - 10000
        await queued.update(status=Status.ACTIVE)
        assert await queue.sweep(lock=1, abort=0.01)
        await reconcile_stalled_jobs()
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            assert job.status == (
                JobStatus.FAILED if attempt_limit == 1 else JobStatus.PENDING
            )
            assert job.retry_count == 1
            updated_at = job.updated_at
        await reconcile_stalled_jobs()
        async with async_session_maker() as session:
            assert (await session.get(Job, job_id)).updated_at == updated_at
    finally:
        await queue.disconnect()


@pytest.mark.asyncio
async def test_missing_or_stale_queue_record_does_not_fail_current_job():
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key="new", status=JobStatus.PROCESSING)
        session.add(job)
        await session.commit()
        job_id = job.id
    await reconcile_attempt(job_id, None)
    await reconcile_attempt(
        job_id,
        QueueJob(
            function="process_pdf_render",
            kwargs={"payload": {"id": job_id, "dispatch_key": "old"}},
            status=Status.FAILED,
            attempts=3,
        ),
    )
    async with async_session_maker() as session:
        assert (await session.get(Job, job_id)).status == JobStatus.PROCESSING


@pytest.mark.asyncio
async def test_success_after_dead_attempt_preserves_attempt_count(store_pdf):
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", status=JobStatus.PROCESSING)
        session.add(job)
        await session.commit()
        job_id = job.id
    store_pdf(job_id)
    await process_pdf_render(
        {"job": QueueJob(function="process_pdf_render", attempts=2)},
        payload={"id": job_id},
    )
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.retry_count == 1


@pytest.mark.asyncio
async def test_stale_queued_snapshot_does_not_overwrite_active_attempt():
    key = uuid4().hex
    queue = Queue.from_url(settings.REDIS_URL, name=f"stale-{key}")
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key=key, status=JobStatus.PROCESSING)
        session.add(job)
        await session.commit()
        job_id = job.id
    try:
        await queue.enqueue(
            "process_pdf_render", key=key, payload={"id": job_id, "dispatch_key": key}
        )
        stale = await queue.job(key)
        active = await queue.dequeue(timeout=1)
        active.attempts = 1
        active.started = now()
        await active.update(status=Status.ACTIVE)
        await reconcile_attempt(job_id, stale)
        async with async_session_maker() as session:
            assert (await session.get(Job, job_id)).status == JobStatus.PROCESSING
    finally:
        await queue.disconnect()


@pytest.mark.asyncio
async def test_graceful_worker_cancellation_returns_job_to_pending(monkeypatch):
    key = uuid4().hex
    queue = Queue.from_url(settings.REDIS_URL, name=f"cancel-{key}")
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key=key, max_retries=2)
        session.add(job)
        await session.commit()
        job_id = job.id
    started = asyncio.Event()

    async def hangs(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.tasks.render_pdf", hangs)
    await queue.enqueue(
        "process_pdf_render",
        key=key,
        payload={"id": job_id, "dispatch_key": key},
        retries=2,
        timeout=10,
    )
    worker = Worker(
        queue,
        functions=worker_settings["functions"],
        after_process=worker_settings["after_process"],
        concurrency=1,
    )
    processing = asyncio.create_task(worker.process())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        processing.cancel()
        await asyncio.wait_for(processing, timeout=5)
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            assert job.status == JobStatus.PENDING
            assert job.retry_count == 1
    finally:
        if not processing.done():
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
        worker.pool.shutdown(wait=True)
        await queue.disconnect()
