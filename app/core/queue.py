from saq import Queue
from app.core.config import settings

queue = Queue.from_url(settings.REDIS_URL, name="tasks")


async def queue_counts(task_queue: Queue) -> dict[str, int]:
    """Read Redis queue counters atomically, including delayed retries."""
    async with task_queue.redis.pipeline(transaction=True) as pipeline:
        pipeline.llen(task_queue._queued)
        pipeline.llen(task_queue._active)
        pipeline.zcard(task_queue._incomplete)
        queued, active, incomplete = await pipeline.execute()
    return {
        "queued": queued,
        "active": active,
        "scheduled": max(0, incomplete - queued - active),
    }
