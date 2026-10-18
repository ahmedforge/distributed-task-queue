import asyncio
from uuid import uuid4

import pytest
from saq import Queue

from app.core.config import settings
from app.core.database import async_session_maker
from app.dispatch import dispatch_job, dispatch_pending
from app.models.job import Job, JobStatus
from app.tasks import process_pdf_render


class UnavailableQueue:
    async def enqueue(self, *args, **kwargs):
        raise ConnectionError("Redis unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("requeue", [False, True])
async def test_delivery_recovers_after_api_request_ends(
    client, monkeypatch, pdf_bytes, requeue
):
    monkeypatch.setattr("app.api.v1.jobs.queue", UnavailableQueue())
    if requeue:
        async with async_session_maker() as session:
            job = Job(filename="test.pdf", status=JobStatus.FAILED, retry_count=3)
            session.add(job)
            await session.commit()
            job_id = job.id
        response = await client.post(f"/api/v1/jobs/{job_id}/requeue")
        assert response.status_code == 200
    else:
        response = await client.post(
            "/api/v1/jobs/upload",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 201
        job_id = response.json()["id"]
    assert response.json()["status"] == "PENDING"
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        assert job.dispatch_pending
        key = job.dispatch_key
    test_queue = Queue.from_url(settings.REDIS_URL, name=f"dispatch-{uuid4().hex}")
    monkeypatch.setattr("app.dispatch.queue", test_queue)
    try:
        await dispatch_pending()
        queued = await test_queue.job(key)
        assert queued.kwargs["payload"] == {"id": job_id, "dispatch_key": key}
        async with async_session_maker() as session:
            assert not (await session.get(Job, job_id)).dispatch_pending
    finally:
        await test_queue.disconnect()


@pytest.mark.asyncio
async def test_repeated_delivery_uses_one_queue_key():
    test_queue = Queue.from_url(settings.REDIS_URL, name=f"dispatch-{uuid4().hex}")
    key = uuid4().hex
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key=key, dispatch_pending=True)
        session.add(job)
        await session.commit()
        job_id = job.id
    try:
        # Simulate Redis accepting delivery before the API dies without clearing
        # the durable database marker.
        await test_queue.enqueue(
            "process_pdf_render", key=key, payload={"id": job_id, "dispatch_key": key}
        )
        await dispatch_job(job_id, test_queue)
        assert await test_queue.redis.llen(test_queue._queued) == 1
    finally:
        await test_queue.disconnect()


@pytest.mark.asyncio
async def test_worker_receipt_prevents_redelivery_after_completion(store_pdf):
    key = uuid4().hex
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key=key, dispatch_pending=True)
        session.add(job)
        await session.commit()
        job_id = job.id
    store_pdf(job_id)
    await process_pdf_render({}, payload={"id": job_id, "dispatch_key": key})
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        assert not job.dispatch_pending
        assert job.status == JobStatus.COMPLETED
        updated_at = job.updated_at
    # Even if an old queue record expires, repeat delivery cannot rerender.
    await process_pdf_render({}, payload={"id": job_id, "dispatch_key": key})
    async with async_session_maker() as session:
        assert (await session.get(Job, job_id)).updated_at == updated_at


@pytest.mark.asyncio
async def test_old_generation_does_not_process_requeued_job():
    async with async_session_maker() as session:
        job = Job(
            filename="test.pdf", dispatch_key="new-generation", dispatch_pending=True
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    await process_pdf_render(
        {}, payload={"id": job_id, "dispatch_key": "old-generation"}
    )
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        assert job.dispatch_pending
        assert job.status == JobStatus.PENDING
        assert job.retry_count == 0


@pytest.mark.asyncio
async def test_api_startup_recovers_committed_delivery_marker(monkeypatch):
    from app.main import app

    key = uuid4().hex
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", dispatch_key=key, dispatch_pending=True)
        session.add(job)
        await session.commit()
        job_id = job.id
    test_queue = Queue.from_url(settings.REDIS_URL, name=f"startup-{uuid4().hex}")
    monkeypatch.setattr("app.dispatch.queue", test_queue)
    try:
        async with app.router.lifespan_context(app):
            async with asyncio.timeout(5):
                # Redis enqueue precedes the PostgreSQL commit. Keep the
                # dispatcher alive until both sides finish, or shutdown can
                # cancel the commit and make this test timing-dependent.
                while True:
                    async with async_session_maker() as session:
                        saved = await session.get(Job, job_id)
                        committed = not saved.dispatch_pending
                    if committed and await test_queue.job(key) is not None:
                        break
                    await asyncio.sleep(0.05)
        async with async_session_maker() as session:
            assert not (await session.get(Job, job_id)).dispatch_pending
    finally:
        await test_queue.disconnect()
