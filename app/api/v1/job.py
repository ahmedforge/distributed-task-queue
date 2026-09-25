from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import select
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.job import Job, JobStatus
from app.schemas.job import JobProgressUpdate

router = APIRouter()

@router.websocket("/jobs/{job_id}/ws")
async def job_progress_websocket(websocket: WebSocket, job_id: int):
    await websocket.accept()
    
    # 1. Query initial DB state
    async with async_session_maker() as session:
        statement = select(Job).where(Job.id == job_id)
        result = await session.exec(statement)
        job = result.first()

    if not job:
        await websocket.send_json({"error": f"Job {job_id} not found"})
        await websocket.close(code=4004)
        return

    # 2. Send immediate initial state
    initial_payload = JobProgressUpdate(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        updated_at=job.updated_at
    ).model_dump_json()
    
    await websocket.send_text(initial_payload)

    # 3. Close immediately if job already completed or failed (Late Subscriber)
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        await websocket.close(code=1000)
        return

    # 4. Subscribe to Pub/Sub channel for live updates
    redis_client = aioredis.from_url(settings.REDIS_URL)
    pubsub = redis_client.pubsub()
    channel_name = f"job_progress:{job_id}"
    await pubsub.subscribe(channel_name)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data_str = message["data"].decode("utf-8")
                await websocket.send_text(data_str)
                
                update = JobProgressUpdate.model_validate_json(data_str)
                if update.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    break

    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()
        try:
            await websocket.close()
        except RuntimeError:
            pass