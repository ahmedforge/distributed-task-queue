"""Export API metrics and Redis-aggregated worker measurements."""

import asyncio
import logging

import redis.asyncio as aioredis
from prometheus_client import CollectorRegistry, CONTENT_TYPE_LATEST, generate_latest
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    HistogramMetricFamily,
)
from redis.exceptions import RedisError
from starlette.responses import Response

from app.core.config import settings
from app.core.queue import queue

logger = logging.getLogger(__name__)
HTTP_REGISTRY = CollectorRegistry()
TASK_TYPE = "process_pdf_render"
DURATION_BUCKETS = (0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 1800)


def metrics_key() -> str:
    return f"dtq:{queue.name}:worker_metrics"


async def record_attempt(outcome: str, duration: float, terminal_failure: bool) -> None:
    """Metrics are best effort and never turn a successful render into a failure."""
    try:
        async with aioredis.from_url(
            settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
        ) as redis:
            async with redis.pipeline(transaction=True) as pipeline:
                key = metrics_key()
                pipeline.hincrby(key, f"{TASK_TYPE}:attempts:{outcome}", 1)
                pipeline.hincrby(key, f"{TASK_TYPE}:duration:count", 1)
                pipeline.hincrbyfloat(key, f"{TASK_TYPE}:duration:sum", duration)
                for bound in DURATION_BUCKETS:
                    if duration <= bound:
                        pipeline.hincrby(key, f"{TASK_TYPE}:bucket:{bound}", 1)
                if terminal_failure:
                    pipeline.hincrby(key, f"{TASK_TYPE}:terminal_failures", 1)
                await asyncio.wait_for(pipeline.execute(), timeout=0.5)
    except (RedisError, OSError, TimeoutError):
        logger.warning("Could not record worker metrics", exc_info=True)


class WorkerCollector:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def collect(self):
        health = GaugeMetricFamily(
            "dtq_redis_metrics_available",
            "Whether Redis queue and worker metrics were collected.",
        )
        health.add_metric([], 1 if self.snapshot is not None else 0)
        yield health
        if self.snapshot is None:
            return
        queued, active, incomplete, data = self.snapshot
        values = {key.decode(): float(value) for key, value in data.items()}
        depth = GaugeMetricFamily(
            "dtq_queue_jobs",
            "Current broker jobs by queue and state.",
            labels=["queue", "state"],
        )
        for state, count in [
            ("queued", queued),
            ("active", active),
            ("scheduled", max(0, incomplete - queued - active)),
        ]:
            depth.add_metric([queue.name, state], count)
        yield depth
        attempts = CounterMetricFamily(
            "dtq_task_attempts",
            "Processing attempts, including retries.",
            labels=["queue", "task_type", "outcome"],
        )
        for outcome in ["success", "failure", "cancelled"]:
            attempts.add_metric(
                [queue.name, TASK_TYPE, outcome],
                values.get(f"{TASK_TYPE}:attempts:{outcome}", 0),
            )
        yield attempts
        failures = CounterMetricFamily(
            "dtq_task_terminal_failures",
            "Attempts that exhausted the retry budget and entered FAILED.",
            labels=["queue", "task_type"],
        )
        failures.add_metric(
            [queue.name, TASK_TYPE], values.get(f"{TASK_TYPE}:terminal_failures", 0)
        )
        yield failures
        duration = HistogramMetricFamily(
            "dtq_task_duration_seconds",
            "Processing time per attempt, excluding queue wait and retry delay.",
            labels=["queue", "task_type"],
        )
        count = values.get(f"{TASK_TYPE}:duration:count", 0)
        buckets = [
            (str(bound), values.get(f"{TASK_TYPE}:bucket:{bound}", 0))
            for bound in DURATION_BUCKETS
        ]
        buckets.append(("+Inf", count))
        duration.add_metric(
            [queue.name, TASK_TYPE],
            buckets=buckets,
            sum_value=values.get(f"{TASK_TYPE}:duration:sum", 0),
        )
        yield duration


async def metrics_response() -> Response:
    try:
        async with queue.redis.pipeline(transaction=True) as pipeline:
            pipeline.llen(queue._queued)
            pipeline.llen(queue._active)
            pipeline.zcard(queue._incomplete)
            pipeline.hgetall(metrics_key())
            snapshot = await asyncio.wait_for(pipeline.execute(), timeout=2)
    except (RedisError, OSError, TimeoutError):
        snapshot = None
    registry = CollectorRegistry()
    registry.register(HTTP_REGISTRY)
    registry.register(WorkerCollector(snapshot))
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
