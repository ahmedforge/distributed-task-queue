from app.core.config import settings as config
from app.core.queue import queue
from app.tasks import process_pdf_render
from app.recovery import after_process

settings = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": config.WORKER_CONCURRENCY,
    "shutdown_grace_period_s": config.WORKER_SHUTDOWN_GRACE_SECONDS,
    # Let idle Redis dequeues return so graceful shutdown can finish.
    "dequeue_timeout": 1,
    "after_process": after_process,
}
