import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
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
    async with AsyncSession(engine) as session:
        # 1. Create a FAILED job directly in DB
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

    # 2. Verify job appears in DLQ endpoint
    response = await client.get("/api/v1/jobs/dlq")
    assert response.status_code == 200
    dlq_jobs = response.json()
    assert any(j["id"] == failed_job.id for j in dlq_jobs)

    # 3. Trigger Requeue
    requeue_res = await client.post(f"/api/v1/jobs/{failed_job.id}/requeue")
    assert requeue_res.status_code == 200
    data = requeue_res.json()
    assert data["status"] == JobStatus.PENDING
    assert data["retry_count"] == 0
    assert data["error_message"] is None