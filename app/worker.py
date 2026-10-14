from app.core.queue import queue
from app.tasks import process_pdf_render
from app.recovery import after_process

settings = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": 10,
    "after_process": after_process,
}
