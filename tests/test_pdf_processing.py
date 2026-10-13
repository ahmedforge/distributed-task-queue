import asyncio
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from saq import Queue
from saq.worker import Worker

from app.core.config import settings
from app.core.storage import input_path, result_path
from app.worker import settings as worker_settings


@pytest.mark.asyncio
async def test_upload_render_and_download(client, monkeypatch, two_page_pdf):
    test_queue = Queue.from_url(settings.REDIS_URL, name=f"pdf-test-{uuid4().hex}")
    monkeypatch.setattr("app.api.v1.jobs.queue", test_queue)
    worker = Worker(test_queue, functions=worker_settings["functions"], concurrency=1)
    try:

        async def submit():
            return await client.post(
                "/api/v1/jobs/upload",
                files={
                    "file": ("../../report.pdf", two_page_pdf, "application/pdf"),
                },
                data={"idempotency_key": "pdf-test-key"},
            )

        response = await submit()
        assert response.status_code == 201
        job_id = response.json()["id"]
        assert response.json()["filename"] == "report.pdf"
        assert input_path(job_id).read_bytes() == two_page_pdf
        assert (await submit()).json()["id"] == job_id
        assert (await client.get(f"/api/v1/jobs/{job_id}/result")).status_code == 409

        async with asyncio.timeout(15):
            assert await worker.process()
        job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
        assert job["status"] == "COMPLETED"
        assert job["progress"] == 100
        response = await client.get(f"/api/v1/jobs/{job_id}/result")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with ZipFile(BytesIO(response.content)) as archive:
            assert archive.namelist() == ["page-0001.png", "page-0002.png"]
            assert all(
                archive.read(name).startswith(b"\x89PNG\r\n\x1a\n")
                for name in archive.namelist()
            )
        assert result_path(job_id).is_file()
    finally:
        worker.pool.shutdown(wait=True)
        await test_queue.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("file.txt", b"%PDF-1.4\n"),
        ("file.pdf", b"not a PDF"),
        ("file.pdf", b""),
    ],
)
async def test_invalid_uploads_are_rejected(client, filename, content):
    response = await client.post(
        "/api/v1/jobs/upload",
        files={
            "file": (filename, content, "application/pdf"),
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_oversized_upload_is_rejected(client, monkeypatch, pdf_bytes):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 10)
    response = await client.post(
        "/api/v1/jobs/upload",
        files={
            "file": ("file.pdf", pdf_bytes, "application/pdf"),
        },
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_page_limit_enters_failed_queue(client, monkeypatch, two_page_pdf):
    monkeypatch.setattr(settings, "MAX_PDF_PAGES", 1)
    response = await client.post(
        "/api/v1/jobs/upload",
        files={
            "file": ("file.pdf", two_page_pdf, "application/pdf"),
        },
        data={"max_retries": "1"},
    )
    job_id = response.json()["id"]
    with pytest.raises(ValueError, match="1 to 1 pages"):
        await worker_settings["functions"][0]({}, payload={"id": job_id})
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "FAILED"
    assert not result_path(job_id).exists()


@pytest.mark.asyncio
async def test_filename_only_submissions_explain_upload_requirement(client):
    response = await client.post("/api/v1/jobs", json={"filename": "file.pdf"})
    assert response.status_code == 400
    assert "/upload" in response.json()["detail"]
