import asyncio

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import async_session_maker
from app.core.storage import input_path, result_path, job_directory
from app.deletion import cleanup_pending_deletions
from app.models.job import Job, JobStatus


async def create_job(status):
    async with async_session_maker() as session:
        job = Job(filename="test.pdf", status=status, idempotency_key="delete-test")
        session.add(job)
        await session.commit()
        return job.id


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
async def test_delete_removes_metadata_and_all_saved_files(client, store_pdf, status):
    job_id = await create_job(status)
    store_pdf(job_id)
    result_path(job_id).write_bytes(b"result")
    scratch = job_directory(job_id) / "leftover-scratch"
    scratch.mkdir()
    (scratch / "page.png").write_bytes(b"image")
    response = await client.delete(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json() == {"job_id": job_id, "cleanup_pending": False}
    assert not job_directory(job_id).exists()
    assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 404
    assert (await client.get(f"/api/v1/jobs/{job_id}/result")).status_code == 404
    assert (await client.get("/api/v1/jobs")).json()["total"] == 0
    async with async_session_maker() as session:
        assert await session.get(Job, job_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.PROCESSING])
async def test_inflight_jobs_cannot_be_deleted(client, store_pdf, status):
    job_id = await create_job(status)
    store_pdf(job_id)
    response = await client.delete(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 409
    assert input_path(job_id).exists()
    async with async_session_maker() as session:
        assert not (await session.get(Job, job_id)).delete_pending


@pytest.mark.asyncio
async def test_cleanup_failure_is_durable_and_retries(client, monkeypatch, store_pdf):
    from app.deletion import remove_job_files

    job_id = await create_job(JobStatus.FAILED)
    store_pdf(job_id)

    def denied(job_id):
        raise PermissionError("Storage temporarily unavailable")

    monkeypatch.setattr("app.deletion.remove_job_files", denied)
    response = await client.delete(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 202
    assert response.json()["cleanup_pending"]
    assert input_path(job_id).exists()
    assert (await client.get("/api/v1/jobs")).json()["total"] == 0
    assert (await client.get("/api/v1/jobs/dlq")).json() == []
    assert (await client.get("/api/v1/jobs/stats")).json()["total_jobs"] == 0
    assert (await client.post(f"/api/v1/jobs/{job_id}/requeue")).status_code == 404
    async with async_session_maker() as session:
        assert (await session.get(Job, job_id)).delete_pending
    monkeypatch.setattr("app.deletion.remove_job_files", remove_job_files)
    await cleanup_pending_deletions()
    assert not job_directory(job_id).exists()
    async with async_session_maker() as session:
        assert await session.get(Job, job_id) is None


@pytest.mark.asyncio
async def test_cleanup_resumes_after_crash_between_file_removal_and_db_commit(
    monkeypatch, store_pdf
):
    from app.deletion import cleanup_job

    job_id = await create_job(JobStatus.COMPLETED)
    store_pdf(job_id)
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        job.delete_pending = True
        await session.commit()
    original_commit = AsyncSession.commit

    async def lost_commit(self):
        raise RuntimeError("Simulated crash after removing files")

    monkeypatch.setattr(AsyncSession, "commit", lost_commit)
    assert not await cleanup_job(job_id)
    assert not job_directory(job_id).exists()
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    await cleanup_pending_deletions()
    async with async_session_maker() as session:
        assert await session.get(Job, job_id) is None


@pytest.mark.asyncio
async def test_delete_handles_missing_files_and_missing_job(client):
    job_id = await create_job(JobStatus.COMPLETED)
    assert (await client.delete(f"/api/v1/jobs/{job_id}")).status_code == 200
    assert (await client.delete(f"/api/v1/jobs/{job_id}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [True, False])
async def test_delete_and_requeue_cannot_remove_files_from_claimed_job(
    client, monkeypatch, store_pdf, delete_first
):
    job_id = await create_job(JobStatus.FAILED)
    store_pdf(job_id)

    async def dispatched(*args):
        return True

    monkeypatch.setattr("app.api.v1.jobs.dispatch_job", dispatched)
    delete = lambda: client.delete(f"/api/v1/jobs/{job_id}")
    requeue = lambda: client.post(f"/api/v1/jobs/{job_id}/requeue")
    operations = [delete, requeue] if delete_first else [requeue, delete]
    results = await asyncio.gather(*(operation() for operation in operations))
    deleted, requeued = results if delete_first else results[::-1]
    assert (deleted.status_code, requeued.status_code) in [(200, 404), (409, 200)]
    if requeued.status_code == 200:
        assert input_path(job_id).exists()
        assert (await client.get(f"/api/v1/jobs/{job_id}")).json()[
            "status"
        ] == "PENDING"
    else:
        assert not job_directory(job_id).exists()
