import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_create_job_and_verify_idempotency():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "idempotency_key": "test-key-day6-001",
            "job_type": "pdf_render",
            "payload": {"id": 888}
        }
        
        # 1. Create Job
        response = await client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["idempotency_key"] == "test-key-day6-001"
        assert data["status"] == "PENDING"
        job_id = data["id"]

        # 2. Duplicate Call Returns Existing Record (Idempotency)
        dup_response = await client.post("/api/v1/jobs", json=payload)
        assert dup_response.status_code == 201
        assert dup_response.json()["id"] == job_id

        # 3. Fetch Status via GET Route
        get_response = await client.get(f"/api/v1/jobs/{job_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == job_id

@pytest.mark.asyncio
async def test_get_nonexistent_job():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        response = await client.get(f"/api/v1/jobs/{fake_uuid}")
        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
