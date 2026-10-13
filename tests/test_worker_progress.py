import asyncio

import pytest
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.schemas.job import JobProgressUpdate
from app.worker import settings as worker_settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('filename', 'expected_status', 'expected_progress'),
    [
        ('valid.pdf', JobStatus.COMPLETED, [10, 40, 75, 100]),
        ('corrupted.pdf', JobStatus.FAILED, [10, 10]),
    ],
)
async def test_registered_worker_publishes_persisted_progress(
    filename, expected_status, expected_progress
):
    async with async_session_maker() as session:
        job = Job(filename=filename, max_retries=1)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    async with aioredis.from_url(settings.REDIS_URL) as redis_client:
        async with redis_client.pubsub() as pubsub:
            await pubsub.subscribe(f'job_progress:{job_id}')
            # Await the subscription acknowledgement before starting the worker.
            async with asyncio.timeout(5):
                while True:
                    message = await pubsub.get_message(timeout=1)
                    if message and message['type'] == 'subscribe':
                        break

            task = worker_settings['functions'][0]
            if expected_status == JobStatus.FAILED:
                with pytest.raises(ValueError, match='Corrupted PDF'):
                    await task({}, payload={'id': job_id, 'filename': filename})
            else:
                await task({}, payload={'id': job_id, 'filename': filename})

            updates = []
            async with asyncio.timeout(5):
                while len(updates) < len(expected_progress):
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1
                    )
                    if message:
                        updates.append(
                            JobProgressUpdate.model_validate_json(message['data'])
                        )

    assert [update.progress for update in updates] == expected_progress
    assert all(update.job_id == job_id for update in updates)
    assert all(update.status == JobStatus.PROCESSING for update in updates[:-1])
    assert updates[-1].status == expected_status
    async with async_session_maker() as session:
        persisted = await session.get(Job, job_id)
        assert persisted.status == updates[-1].status
        assert persisted.progress == updates[-1].progress
        assert persisted.updated_at == updates[-1].updated_at


@pytest.mark.asyncio
async def test_progress_notification_outage_does_not_fail_job(monkeypatch):
    async with async_session_maker() as session:
        job = Job(filename='valid.pdf')
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    def unavailable_redis(*args, **kwargs):
        raise ConnectionError('Notification service unavailable')

    monkeypatch.setattr('app.tasks.aioredis.from_url', unavailable_redis)
    await worker_settings['functions'][0](
        {}, payload={'id': job_id, 'filename': 'valid.pdf'}
    )

    async with async_session_maker() as session:
        persisted = await session.get(Job, job_id)
        assert persisted.status == JobStatus.COMPLETED
        assert persisted.progress == 100
        assert persisted.retry_count == 0
