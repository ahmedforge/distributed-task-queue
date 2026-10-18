from app.core.config import settings as config
from app.core.queue import queue
from app.tasks import process_pdf_render
from app.recovery import after_process

settings = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": config.WORKER_CONCURRENCY,
    "after_process": after_process,
}
