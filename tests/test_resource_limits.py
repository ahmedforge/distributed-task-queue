"""Real database races and storage checks for admission, expiry and cleanup."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import select
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.storage import input_path, result_path, get_storage
from app.deletion import cleanup_pending_deletions, expire_finished_jobs
from app.models.account import Account
from app.models.job import Job, JobStatus
from app.worker import settings as worker_settings
from test_accounts import auth_client, register, upload  # noqa: F401


async def finish(job_id, status=JobStatus.COMPLETED, age=None):
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        job.status = status
        job.result_bytes = 0
        job.result_reserved_bytes = 0
        if age is not None:
            job.expires_at = datetime.now(timezone.utc) + age
        await session.commit()


async def post_pdf(client, pdf_bytes, key=None):
    return await client.post(
        "/api/v1/jobs/upload",
        files={"file": ("quota.pdf", pdf_bytes, "application/pdf")},
        data={"idempotency_key": key} if key else {},
    )


@pytest.mark.asyncio
async def test_concurrent_uploads_cannot_overspend_active_slot(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(settings, "ACCOUNT_MAX_ACTIVE_JOBS", 1)
    results = await asyncio.gather(
        *(post_pdf(auth_client, pdf_bytes) for _ in range(5))
    )
    assert sorted(r.status_code for r in results) == [201, 429, 429, 429, 429]
    usage = (await auth_client.get("/api/v1/jobs/usage")).json()
    assert usage["active_jobs"] == usage["retained_jobs"] == 1


@pytest.mark.asyncio
async def test_idempotent_retries_still_work_at_capacity(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(settings, "ACCOUNT_MAX_ACTIVE_JOBS", 1)
    results = await asyncio.gather(
        *(post_pdf(auth_client, pdf_bytes, "one-job") for _ in range(4))
    )
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()["id"] for r in results}) == 1
    assert (await post_pdf(auth_client, pdf_bytes, "different-job")).status_code == 429


@pytest.mark.asyncio
async def test_each_account_has_independent_limits(auth_client, monkeypatch, pdf_bytes):
    await register(auth_client)
    monkeypatch.setattr(settings, "ACCOUNT_MAX_ACTIVE_JOBS", 1)
    await upload(auth_client, pdf_bytes)
    await register(auth_client)
    assert (await post_pdf(auth_client, pdf_bytes)).status_code == 201
    assert (await auth_client.get("/api/v1/jobs/usage")).json()["retained_jobs"] == 1


@pytest.mark.asyncio
async def test_storage_reservations_serialize_concurrent_admission(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(
        settings,
        "ACCOUNT_MAX_STORAGE_BYTES",
        len(pdf_bytes) + settings.MAX_RESULT_BYTES,
    )
    results = await asyncio.gather(
        *(post_pdf(auth_client, pdf_bytes) for _ in range(3))
    )
    assert sorted(r.status_code for r in results) == [201, 409, 409]
    usage = (await auth_client.get("/api/v1/jobs/usage")).json()
    assert usage["storage_bytes"] == len(pdf_bytes)
    assert usage["reserved_bytes"] == settings.MAX_RESULT_BYTES
    assert usage["storage_accounted"]


@pytest.mark.asyncio
async def test_saved_job_limit_includes_cleanup_pending(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(settings, "ACCOUNT_MAX_STORED_JOBS", 1)
    job = await upload(auth_client, pdf_bytes)
    await finish(job["id"])
    from app.deletion import remove_job_files

    def denied(_):
        raise PermissionError("synthetic storage outage")

    monkeypatch.setattr("app.deletion.remove_job_files", denied)
    assert (await auth_client.delete(f"/api/v1/jobs/{job['id']}")).status_code == 202
    assert (await auth_client.get("/api/v1/jobs")).json()["total"] == 0
    assert (await post_pdf(auth_client, pdf_bytes)).status_code == 409
    monkeypatch.setattr("app.deletion.remove_job_files", remove_job_files)
    await cleanup_pending_deletions()
    assert (await post_pdf(auth_client, pdf_bytes)).status_code == 201


@pytest.mark.asyncio
async def test_successful_render_releases_reservation_and_measures_zip(
    auth_client, pdf_bytes
):
    await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    await worker_settings["functions"][0]({}, payload={"id": job["id"]})
    usage = (await auth_client.get("/api/v1/jobs/usage")).json()
    assert usage["reserved_bytes"] == 0
    assert (
        usage["storage_bytes"] == len(pdf_bytes) + result_path(job["id"]).stat().st_size
    )
    saved = (await auth_client.get(f"/api/v1/jobs/{job['id']}")).json()
    assert saved["status"] == "COMPLETED"
    expiry = datetime.fromisoformat(saved["expires_at"].replace("Z", "+00:00"))
    assert (
        timedelta(days=6, hours=23)
        < expiry - datetime.now(timezone.utc)
        <= timedelta(days=7)
    )


@pytest.mark.asyncio
async def test_result_cap_prevents_publication_and_retains_failure_budget(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(settings, "MAX_RESULT_BYTES", 100)
    response = await auth_client.post(
        "/api/v1/jobs/upload",
        files={"file": ("small.pdf", pdf_bytes)},
        data={"max_retries": 1},
    )
    assert response.status_code == 201
    job = response.json()
    with pytest.raises(ValueError, match="result size limit"):
        await worker_settings["functions"][0]({}, payload={"id": job["id"]})
    assert not result_path(job["id"]).exists()
    saved = (await auth_client.get(f"/api/v1/jobs/{job['id']}")).json()
    assert saved["status"] == "FAILED" and saved["expires_at"]
    assert (await auth_client.get("/api/v1/jobs/usage")).json()["reserved_bytes"] == 100


@pytest.mark.asyncio
async def test_retry_must_reacquire_active_slot_and_result_budget(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    first = await upload(auth_client, pdf_bytes)
    await finish(first["id"], JobStatus.FAILED)
    second = await upload(auth_client, pdf_bytes)
    monkeypatch.setattr(settings, "ACCOUNT_MAX_ACTIVE_JOBS", 1)
    assert (
        await auth_client.post(f"/api/v1/jobs/{first['id']}/requeue")
    ).status_code == 429
    await finish(second["id"])
    monkeypatch.setattr(settings, "ACCOUNT_MAX_STORAGE_BYTES", 1)
    assert (
        await auth_client.post(f"/api/v1/jobs/{first['id']}/requeue")
    ).status_code == 409
    monkeypatch.setattr(settings, "ACCOUNT_MAX_STORAGE_BYTES", 512 * 1024 * 1024)
    assert (
        await auth_client.post(f"/api/v1/jobs/{first['id']}/requeue")
    ).status_code == 200


@pytest.mark.asyncio
async def test_simultaneous_retry_and_upload_share_one_admission_slot(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    old = await upload(auth_client, pdf_bytes)
    await finish(old["id"], JobStatus.FAILED)
    monkeypatch.setattr(settings, "ACCOUNT_MAX_ACTIVE_JOBS", 1)
    retried, uploaded = await asyncio.gather(
        auth_client.post(f"/api/v1/jobs/{old['id']}/requeue"),
        post_pdf(auth_client, pdf_bytes),
    )
    assert (retried.status_code, uploaded.status_code) in [(200, 429), (429, 201)]
    assert (await auth_client.get("/api/v1/jobs/usage")).json()["active_jobs"] == 1


@pytest.mark.asyncio
async def test_expired_jobs_hidden_before_cleanup_and_cannot_be_requeued(
    auth_client, pdf_bytes
):
    await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    await finish(job["id"], JobStatus.FAILED, timedelta(seconds=-1))
    assert (await auth_client.get("/api/v1/jobs")).json()["total"] == 0
    assert (await auth_client.get("/api/v1/jobs/dlq")).json() == []
    assert (await auth_client.get("/api/v1/jobs/stats")).json()["total_jobs"] == 0
    assert (await auth_client.get(f"/api/v1/jobs/{job['id']}")).status_code == 404
    assert (
        await auth_client.get(f"/api/v1/jobs/{job['id']}/result")
    ).status_code == 404
    assert (
        await auth_client.post(f"/api/v1/jobs/{job['id']}/requeue")
    ).status_code == 404
    assert input_path(job["id"]).exists()
    assert await expire_finished_jobs() == 1
    await cleanup_pending_deletions()
    assert not input_path(job["id"]).exists()
    assert (await auth_client.get("/api/v1/jobs/usage")).json()["retained_jobs"] == 0


@pytest.mark.asyncio
async def test_expiry_cleanup_outage_is_durable(auth_client, monkeypatch, pdf_bytes):
    await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    await finish(job["id"], age=timedelta(seconds=-1))
    from app.deletion import remove_job_files

    def denied(_):
        raise PermissionError("synthetic storage outage")

    monkeypatch.setattr("app.deletion.remove_job_files", denied)
    assert await expire_finished_jobs() == 1
    await cleanup_pending_deletions()
    assert (await auth_client.get("/api/v1/jobs/usage")).json()["retained_jobs"] == 1
    async with async_session_maker() as session:
        assert (await session.get(Job, job["id"])).delete_pending
    monkeypatch.setattr("app.deletion.remove_job_files", remove_job_files)
    await cleanup_pending_deletions()
    assert (await auth_client.get("/api/v1/jobs/usage")).json()["retained_jobs"] == 0


@pytest.mark.asyncio
async def test_retention_does_not_touch_legacy_active_or_unexpired_jobs(
    auth_client, pdf_bytes
):
    await register(auth_client)
    active = await upload(auth_client, pdf_bytes)
    recent = await upload(auth_client, pdf_bytes)
    await finish(recent["id"], age=timedelta(days=1))
    async with async_session_maker() as session:
        legacy = Job(filename="legacy.pdf", status=JobStatus.FAILED)
        session.add(legacy)
        current = await session.get(Job, active["id"])
        current.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await session.commit()
    assert await expire_finished_jobs() == 0
    async with async_session_maker() as session:
        assert len((await session.exec(select(Job))).all()) == 3


@pytest.mark.asyncio
async def test_retry_resets_expiry_but_keeps_original_retention_policy(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 2)
    job = await upload(auth_client, pdf_bytes)
    await finish(job["id"], JobStatus.FAILED, timedelta(days=1))
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 30)
    retried = await auth_client.post(f"/api/v1/jobs/{job['id']}/requeue")
    assert retried.status_code == 200 and retried.json()["expires_at"] is None
    await worker_settings["functions"][0]({}, payload={"id": job["id"]})
    saved = (await auth_client.get(f"/api/v1/jobs/{job['id']}")).json()
    expiry = datetime.fromisoformat(saved["expires_at"].replace("Z", "+00:00"))
    assert (
        timedelta(days=1, hours=23)
        < expiry - datetime.now(timezone.utc)
        <= timedelta(days=2)
    )


@pytest.mark.asyncio
async def test_retention_can_be_disabled_for_new_jobs(
    auth_client, monkeypatch, pdf_bytes
):
    await register(auth_client)
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 0)
    job = await upload(auth_client, pdf_bytes)
    await worker_settings["functions"][0]({}, payload={"id": job["id"]})
    assert (await auth_client.get(f"/api/v1/jobs/{job['id']}")).json()[
        "expires_at"
    ] is None


@pytest.mark.asyncio
async def test_unknown_legacy_storage_blocks_admission_until_measured(
    auth_client, pdf_bytes
):
    username = await register(auth_client)
    async with async_session_maker() as session:
        account = (
            await session.exec(select(Account).where(Account.username == username))
        ).one()
        old = Job(filename="old.pdf", status=JobStatus.FAILED, owner_id=account.id)
        session.add(old)
        await session.commit()
        old_id = old.id
    input_path(old_id).parent.mkdir(parents=True)
    input_path(old_id).write_bytes(pdf_bytes)
    result_path(old_id).write_bytes(b"legacy zip")
    assert not (await auth_client.get("/api/v1/jobs/usage")).json()["storage_accounted"]
    assert (await post_pdf(auth_client, pdf_bytes)).status_code == 409
    from scripts.measure_job_storage import measure_finished_jobs

    assert await measure_finished_jobs() == 1
    assert not (await auth_client.get("/api/v1/jobs/usage")).json()["storage_accounted"]
    assert await measure_finished_jobs(apply=True) == 1
    usage = (await auth_client.get("/api/v1/jobs/usage")).json()
    assert usage["storage_accounted"] and usage["storage_bytes"] == len(pdf_bytes) + 10
    assert (await post_pdf(auth_client, pdf_bytes)).status_code == 201


@pytest.mark.asyncio
async def test_measurement_outage_keeps_unknown_usage(auth_client, monkeypatch):
    username = await register(auth_client)
    async with async_session_maker() as session:
        owner = (
            await session.exec(select(Account).where(Account.username == username))
        ).one()
        session.add(Job(filename="old.pdf", status=JobStatus.FAILED, owner_id=owner.id))
        await session.commit()

    def unavailable(*_):
        raise OSError("storage unavailable")

    monkeypatch.setattr("app.core.storage.LocalStorage.size", unavailable)
    from scripts.measure_job_storage import measure_finished_jobs

    with pytest.raises(OSError):
        await measure_finished_jobs(apply=True)
    assert not (await auth_client.get("/api/v1/jobs/usage")).json()["storage_accounted"]
