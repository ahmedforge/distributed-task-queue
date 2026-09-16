from app.core.queue import queue
from app.tasks import process_pdf_render

settings = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": 10,
}