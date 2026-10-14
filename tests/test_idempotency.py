import asyncio
from uuid import uuid4

import pytest
from saq import Queue
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.storage import input_path


@pytest.mark.asyncio
async def test_simultaneous_uploads_return_one_job_and_one_task(
    client, monkeypatch, pdf_bytes
):
    queue = Queue.from_url(settings.REDIS_URL, name=f"idempotency-{uuid4().hex}")
    monkeypatch.setattr("app.api.v1.jobs.queue", queue)
    original_read = UploadFile.read
    both_uploading = asyncio.Event()
    readers = 0

    async def synchronized_read(self, size=-1):
        nonlocal readers
        data = await original_read(self, size)
        if data:
            readers += 1
            if readers == 2:
                both_uploading.set()
            # Both requests have already missed the initial key lookup.
            await asyncio.wait_for(both_uploading.wait(), timeout=5)
        return data

    monkeypatch.setattr(UploadFile, "read", synchronized_read)
    inputs = {"first.pdf": pdf_bytes, "second.pdf": pdf_bytes + b"\n"}

    async def upload(name):
        return await client.post(
            "/api/v1/jobs/upload",
            files={"file": (name, inputs[name], "application/pdf")},
            data={"idempotency_key": "simultaneous-key"},
        )

    try:
        responses = await asyncio.gather(
            upload("first.pdf"), upload("second.pdf"), return_exceptions=True
        )
        # Wait for both requests even if the old implementation raises, so
        # fixture teardown cannot race with a still-running winning upload.
        assert all(not isinstance(r, BaseException) for r in responses), responses
        assert [r.status_code for r in responses] == [201, 201]
        jobs = [r.json() for r in responses]
        assert jobs[0]["id"] == jobs[1]["id"]
        assert jobs[0]["filename"] == jobs[1]["filename"]
        assert input_path(jobs[0]["id"]).read_bytes() == inputs[jobs[0]["filename"]]
        listing = (await client.get("/api/v1/jobs")).json()
        assert listing["total"] == 1
        assert await queue.redis.llen(queue._queued) == 1
        # Only the winning job directory remains; losing temporary uploads
        # are removed in the endpoint's finally block.
        assert list(input_path(jobs[0]["id"]).parent.parent.iterdir()) == [
            input_path(jobs[0]["id"]).parent
        ]
    finally:
        await queue.disconnect()


@pytest.mark.asyncio
async def test_different_keys_and_unkeyed_uploads_create_separate_jobs(
    client, pdf_bytes
):
    ids = []
    for key in ["first", "second", None, None]:
        response = await client.post(
            "/api/v1/jobs/upload",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
            data={"idempotency_key": key} if key else {},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    assert len(set(ids)) == 4
