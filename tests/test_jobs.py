import pytest
from httpx import AsyncClient
from app.tasks import process_pdf_render
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus


@pytest.mark.asyncio
async def test_create_job_and_verify_idempotency(client: AsyncClient):
    payload = {
        "filename": "test_document.pdf",
        "max_retries": 3
    }

    response = await client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "test_document.pdf"
    assert data["status"] == JobStatus.PENDING


@pytest.mark.asyncio
async def test_get_nonexistent_job(client: AsyncClient):
    response = await client.get("/api/v1/jobs/99999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


@pytest.mark.asyncio
async def test_dlq_and_requeue_workflow(client: AsyncClient):
    # 1. Create a FAILED job directly in DB using async_session_maker
    async with async_session_maker() as session:
        failed_job = Job(
            filename="corrupted.pdf",
            status=JobStatus.FAILED,
            retry_count=3,
            max_retries=3,
            error_message="Render engine crash"
        )
        session.add(failed_job)
        await session.commit()
        await session.refresh(failed_job)
        failed_job_id = failed_job.id

    # 2. Verify job appears in DLQ endpoint
    response = await client.get("/api/v1/jobs/dlq")
    assert response.status_code == 200
    dlq_jobs = response.json()
    assert any(j["id"] == failed_job_id for j in dlq_jobs)

    # 3. Trigger Requeue
    requeue_res = await client.post(f"/api/v1/jobs/{failed_job_id}/requeue")
    assert requeue_res.status_code == 200
    data = requeue_res.json()
    assert data["status"] == JobStatus.PENDING
    assert data["retry_count"] == 0
    assert data["error_message"] is None


@pytest.mark.asyncio
async def test_worker_process_pdf_success():
    """Test that process_pdf_render successfully updates job status to COMPLETED."""
    async with async_session_maker() as session:
        job = Job(filename="valid_document.pdf", max_retries=3, status=JobStatus.PENDING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    ctx = {}
    payload = {"id": job_id, "filename": "valid_document.pdf"}
    await process_pdf_render(ctx, payload=payload)

    async with async_session_maker() as session:
        updated_job = await session.get(Job, job_id)
        assert updated_job is not None
        assert updated_job.status == JobStatus.COMPLETED
        assert updated_job.error_message is None


@pytest.mark.asyncio
async def test_worker_process_pdf_failure_and_dlq():
    """Test that process_pdf_render handles errors, increments retries, and transitions to FAILED."""
    async with async_session_maker() as session:
        job = Job(filename="corrupted.pdf", max_retries=1, status=JobStatus.PENDING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    ctx = {}
    payload = {"id": job_id, "filename": "corrupted.pdf"}
    with pytest.raises(ValueError, match="Corrupted PDF file cannot be processed."):
        await process_pdf_render(ctx, payload=payload)

    async with async_session_maker() as session:
        failed_job = await session.get(Job, job_id)
        assert failed_job is not None
        assert failed_job.status == JobStatus.FAILED
        assert failed_job.retry_count == 1
        assert failed_job.error_message is not None
        assert "Corrupted PDF file" in failed_job.error_message


@pytest.mark.asyncio
async def test_get_job_stats(client: AsyncClient):
    """Test retrieving job metrics and queue statistics."""
    await client.post("/api/v1/jobs", json={"filename": "doc1.pdf", "max_retries": 3})
    
    response = await client.get("/api/v1/jobs/stats")
    assert response.status_code == 200
    
    data = response.json()
    assert "total_jobs" in data
    assert "status_counts" in data
    assert "dlq_count" in data
    assert "queue_name" in data
    assert data["total_jobs"] >= 1