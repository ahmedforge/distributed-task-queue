"""Idle workers must not spend the full job drain grace blocked on Redis."""

import asyncio
from uuid import uuid4

import pytest
from saq import Queue
from saq.worker import Worker

from app.core.config import settings
from app.worker import settings as worker_settings


@pytest.mark.asyncio
async def test_idle_worker_exits_before_job_drain_deadline():
    queue = Queue.from_url(settings.REDIS_URL, name=f"shutdown-{uuid4().hex}")
    worker = Worker(
        queue,
        functions=worker_settings["functions"],
        concurrency=1,
        dequeue_timeout=worker_settings["dequeue_timeout"],
        shutdown_grace_period_s=10,
        timers={"abort": 1, "schedule": 1, "sweep": 1, "worker_info": 1},
    )
    # Test direct stop without replacing pytest's process signal handlers.
    worker.SIGNALS = []
    running = asyncio.create_task(worker.start())
    try:
        await asyncio.sleep(0.2)
        # With an infinite Redis dequeue, this waits the full ten-second grace.
        await asyncio.wait_for(worker.stop(), timeout=4)
        await asyncio.wait_for(running, timeout=2)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await queue.disconnect()
