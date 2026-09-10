import asyncio
import logging
from app.core.queue import queue

logger = logging.getLogger("saq")

async def process_pdf_render(ctx, *, payload: dict) -> dict:
    job_id = payload.get("id")
    logger.info(f"==> [WORKER] Processing PDF render job #{job_id}...")
    
    # Simulate heavy background work
    await asyncio.sleep(3)
    
    logger.info(f"==> [WORKER] Completed PDF render job #{job_id}")
    return {"status": "completed", "pdf_id": job_id}

# Worker config object loaded by SAQ CLI
SETTINGS = {
    "queue": queue,
    "functions": [process_pdf_render],
    "concurrency": 5,
}