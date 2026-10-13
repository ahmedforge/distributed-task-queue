from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.core.database import async_session_maker
from app.models.job import Job, JobStatus


@pytest_asyncio.fixture
async def seeded_jobs():
    # Equal timestamps verify the ID tie-breaker keeps pagination deterministic.
    created = datetime(2026, 10, 1, tzinfo=timezone.utc)
    statuses = [
        JobStatus.PENDING,
        JobStatus.FAILED,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.PROCESSING,
    ]
    async with async_session_maker() as session:
        jobs = [
            Job(filename=f"file-{index}.pdf", status=status, created_at=created)
            for index, status in enumerate(statuses)
        ]
        session.add_all(jobs)
        await session.commit()
        return jobs


@pytest.mark.asyncio
async def test_empty_job_list(client):
    response = await client.get("/api/v1/jobs")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


@pytest.mark.asyncio
async def test_pagination_has_stable_order_and_no_duplicates(client, seeded_jobs):
    ids = []
    for page in range(1, 4):
        response = await client.get(
            "/api/v1/jobs", params={"page": page, "page_size": 2}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["page"] == page
        assert data["page_size"] == 2
        ids.extend(job["id"] for job in data["items"])
    assert ids == [job.id for job in reversed(seeded_jobs)]
    assert len(set(ids)) == 5
    data = (await client.get("/api/v1/jobs?page=4&page_size=2")).json()
    assert data["items"] == []
    assert data["total"] == 5


@pytest.mark.asyncio
async def test_status_filter_counts_only_matching_jobs(client, seeded_jobs):
    response = await client.get("/api/v1/jobs?status=FAILED&page_size=1")
    data = response.json()
    assert response.status_code == 200
    assert data["total"] == 2
    assert data["items"][0]["id"] == seeded_jobs[3].id
    assert data["items"][0]["status"] == "FAILED"
    response = await client.get("/api/v1/jobs?status=FAILED&page_size=1&page=2")
    assert response.json()["items"][0]["id"] == seeded_jobs[1].id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["page=0", "page_size=0", "page_size=101", "status=UNKNOWN"]
)
async def test_invalid_listing_parameters_are_rejected(client, query):
    response = await client.get(f"/api/v1/jobs?{query}")
    assert response.status_code == 422
