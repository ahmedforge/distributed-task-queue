from uuid import uuid4

import pytest
import pytest_asyncio
from prometheus_client.parser import text_string_to_metric_families
from saq import Queue

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.observability import record_attempt
from app.tasks import process_pdf_render


def sample(body, name, labels=None):
    for family in text_string_to_metric_families(body):
        for item in family.samples:
            if item.name == name and all(
                item.labels.get(key) == value for key, value in (labels or {}).items()
            ):
                return item.value
    raise AssertionError(f"Metric sample {name} was not exported")


@pytest_asyncio.fixture
async def metric_queue(monkeypatch):
    queue = Queue.from_url(settings.REDIS_URL, name=f"obs-test-{uuid4().hex}")
    monkeypatch.setattr("app.observability.queue", queue)
    yield queue
    await queue.disconnect()


@pytest.mark.asyncio
async def test_api_request_metrics_use_route_templates(client, metric_queue):
    await client.get("/health")
    await client.get("/api/v1/jobs/99999")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert (
        sample(
            response.text,
            "http_requests_total",
            {"handler": "/health", "status": "200"},
        )
        >= 1
    )
    assert (
        sample(
            response.text,
            "http_requests_total",
            {"handler": "/api/v1/jobs/{job_id}", "status": "404"},
        )
        >= 1
    )
    assert 'handler="/api/v1/jobs/99999"' not in response.text
    assert 'handler="/metrics"' not in response.text


@pytest.mark.asyncio
async def test_redis_counters_and_duration_histogram(client, metric_queue):
    await record_attempt("success", 0.25, False)
    await record_attempt("failure", 0.8, True)
    response = await client.get("/metrics")
    assert sample(response.text, "dtq_task_attempts_total", {"outcome": "success"}) == 1
    assert sample(response.text, "dtq_task_attempts_total", {"outcome": "failure"}) == 1
    assert sample(response.text, "dtq_task_terminal_failures_total") == 1
    assert sample(response.text, "dtq_task_duration_seconds_count") == 2
    assert sample(response.text, "dtq_task_duration_seconds_sum") == pytest.approx(1.05)
    assert sample(response.text, "dtq_task_duration_seconds_bucket", {"le": "0.1"}) == 0
    assert sample(response.text, "dtq_task_duration_seconds_bucket", {"le": "0.5"}) == 1
    assert (
        sample(response.text, "dtq_task_duration_seconds_bucket", {"le": "+Inf"}) == 2
    )


@pytest.mark.asyncio
async def test_real_worker_success_and_exhausted_failure_are_measured(
    client, metric_queue, store_pdf
):
    async with async_session_maker() as session:
        valid = Job(filename="valid.pdf")
        invalid = Job(filename="invalid.pdf", max_retries=1)
        session.add_all([valid, invalid])
        await session.commit()
        store_pdf(valid.id)
        store_pdf(invalid.id, corrupted=True)
    await process_pdf_render({}, payload={"id": valid.id})
    with pytest.raises(ValueError):
        await process_pdf_render({}, payload={"id": invalid.id})
    body = (await client.get("/metrics")).text
    assert sample(body, "dtq_task_attempts_total", {"outcome": "success"}) == 1
    assert sample(body, "dtq_task_attempts_total", {"outcome": "failure"}) == 1
    assert sample(body, "dtq_task_terminal_failures_total") == 1
    assert sample(body, "dtq_task_duration_seconds_count") == 2
    assert sample(body, "dtq_task_duration_seconds_sum") > 0


@pytest.mark.asyncio
async def test_metrics_outage_does_not_fail_successful_render(monkeypatch, store_pdf):
    async with async_session_maker() as session:
        job = Job(filename="valid.pdf")
        session.add(job)
        await session.commit()
        job_id = job.id
    store_pdf(job_id)

    def unavailable(*args, **kwargs):
        raise OSError("Metrics Redis is unavailable")

    monkeypatch.setattr("app.observability.aioredis.from_url", unavailable)
    await process_pdf_render({}, payload={"id": job_id})
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.error_message is None


@pytest.mark.asyncio
async def test_unavailable_worker_metrics_are_omitted_not_zero(client, monkeypatch):
    queue = Queue.from_url("redis://127.0.0.1:1", name="unavailable")
    monkeypatch.setattr("app.observability.queue", queue)
    try:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert sample(response.text, "dtq_redis_metrics_available") == 0
        assert "dtq_queue_jobs" not in response.text
        assert "dtq_task_attempts_total" not in response.text
    finally:
        await queue.redis.aclose()


@pytest.mark.asyncio
async def test_terminal_failure_is_counted_only_after_persisting_failed_state(
    client, monkeypatch, metric_queue, store_pdf
):
    from app.tasks import report_progress

    async with async_session_maker() as session:
        job = Job(filename="invalid.pdf", max_retries=1)
        session.add(job)
        await session.commit()
        job_id = job.id
    store_pdf(job_id, corrupted=True)

    async def fail_terminal_commit(session, job, progress, status):
        if status == JobStatus.FAILED:
            raise RuntimeError("simulated persist failure")
        await report_progress(session, job, progress, status)

    monkeypatch.setattr("app.tasks.report_progress", fail_terminal_commit)
    with pytest.raises(RuntimeError, match="simulated persist failure"):
        await process_pdf_render({}, payload={"id": job_id})
    body = (await client.get("/metrics")).text
    assert sample(body, "dtq_task_attempts_total", {"outcome": "failure"}) == 1
    assert sample(body, "dtq_task_terminal_failures_total") == 0
