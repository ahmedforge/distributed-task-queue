"""Real MinIO integration coverage, opt-in locally and enabled in CI."""

import os
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from app.core.config import settings
from app.core.storage import S3Storage
from app.core.database import async_session_maker
from app.models.job import Job
from app.worker import settings as worker_settings
from app.deletion import cleanup_pending_deletions


@pytest.fixture
def object_storage(monkeypatch):
    endpoint = os.environ.get("DTQ_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Set DTQ_TEST_S3_ENDPOINT to run real MinIO integration tests")
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", endpoint)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "dtq-test")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "dtq-test-password")
    monkeypatch.setattr(settings, "S3_BUCKET", f"test-{uuid4().hex}")
    storage = S3Storage()
    storage.client.create_bucket(Bucket=storage.bucket)
    yield storage
    objects = storage.client.list_objects_v2(Bucket=storage.bucket).get("Contents", [])
    for obj in objects:
        storage.client.delete_object(Bucket=storage.bucket, Key=obj["Key"])
    storage.client.delete_bucket(Bucket=storage.bucket)


@pytest.mark.asyncio
async def test_shared_storage_end_to_end(
    client, monkeypatch, two_page_pdf, tmp_path, object_storage
):
    response = await client.post(
        "/api/v1/jobs/upload",
        files={"file": ("test.pdf", two_page_pdf, "application/pdf")},
    )
    assert response.status_code == 201
    job_id = response.json()["id"]
    # Simulate a different worker host: no access to the API's storage directory.
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "worker-only"))
    await worker_settings["functions"][0]({}, payload={"id": job_id})
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "api-only"))
    response = await client.get(f"/api/v1/jobs/{job_id}/result")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert all(
            archive.read(n).startswith(b"\x89PNG\r\n\x1a\n") for n in archive.namelist()
        )
    assert not (tmp_path / "worker-only").exists()
    assert not (tmp_path / "api-only").exists()
    original_fetch = S3Storage.fetch

    def missing(self, *args):
        raise FileNotFoundError("pages.zip")

    monkeypatch.setattr(S3Storage, "fetch", missing)
    assert (await client.get(f"/api/v1/jobs/{job_id}/result")).status_code == 404

    def offline(self, *args):
        raise OSError("Object store unavailable")

    monkeypatch.setattr(S3Storage, "fetch", offline)
    assert (await client.get(f"/api/v1/jobs/{job_id}/result")).status_code == 503
    monkeypatch.setattr(S3Storage, "fetch", original_fetch)
    # An unavailable object store must retain the durable deletion marker.
    original = S3Storage.delete_job

    def unavailable(self, job_id):
        self.client.delete_object(Bucket=self.bucket, Key=f"{job_id}/input.pdf")
        raise OSError("Object store unavailable after partial cleanup")

    monkeypatch.setattr(S3Storage, "delete_job", unavailable)
    assert (await client.delete(f"/api/v1/jobs/{job_id}")).status_code == 202
    monkeypatch.setattr(S3Storage, "delete_job", original)
    await cleanup_pending_deletions()
    assert (
        object_storage.client.list_objects_v2(Bucket=object_storage.bucket).get(
            "KeyCount", 0
        )
        == 0
    )
    async with async_session_maker() as session:
        assert await session.get(Job, job_id) is None


@pytest.mark.asyncio
async def test_output_publish_failure_does_not_complete_job(
    client, monkeypatch, pdf_bytes, object_storage
):
    response = await client.post(
        "/api/v1/jobs/upload",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"max_retries": 1},
    )
    job_id = response.json()["id"]

    def unavailable(self, *args):
        raise OSError("Object store unavailable")

    monkeypatch.setattr(S3Storage, "put", unavailable)
    with pytest.raises(OSError):
        await worker_settings["functions"][0]({}, payload={"id": job_id})
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["status"] == "FAILED"
    assert (await client.get(f"/api/v1/jobs/{job_id}/result")).status_code == 409


@pytest.mark.asyncio
async def test_upload_storage_failure_rolls_back_job(
    client, monkeypatch, pdf_bytes, object_storage
):
    from sqlmodel import select

    def unavailable(self, *args):
        raise OSError("Object store unavailable")

    monkeypatch.setattr(S3Storage, "put", unavailable)
    response = await client.post(
        "/api/v1/jobs/upload",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"idempotency_key": "storage-failure"},
    )
    assert response.status_code == 503
    async with async_session_maker() as session:
        assert (
            await session.exec(
                select(Job).where(Job.idempotency_key == "storage-failure")
            )
        ).first() is None
