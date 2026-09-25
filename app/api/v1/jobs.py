from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import col, func, select
from app.core.database import get_session
from app.core.queue import queue
from app.models.job import Job, JobCreate, JobRead, JobStatus
from app.schemas.job import JobStatsResponse
import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect
from app.core.config import settings


router = APIRouter()


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=JobRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_job(job_in: JobCreate, session: AsyncSession = Depends(get_session)):
    if job_in.idempotency_key:
        statement = select(Job).where(Job.idempotency_key == job_in.idempotency_key)
        existing = (await session.exec(statement)).first()
        if existing:
            return existing

    job = Job.model_validate(job_in)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    await queue.enqueue(
        "process_pdf_render",
        payload={"id": job.id, "filename": job.filename}
    )
    return job


@router.get("/stats", response_model=JobStatsResponse)
async def get_job_stats(session: AsyncSession = Depends(get_session)):
    """Fetch aggregated job metrics across database and Redis queue."""
    # 1. Aggregate database counts by status
    statement = select(Job.status, func.count(col(Job.id))).group_by(Job.status)
    results = await session.exec(statement)
    raw_counts = dict(results.all())

    # Map counts across all defined statuses
    status_counts = {
        status.value: raw_counts.get(status, 0)
        for status in JobStatus
    }

    total_jobs = sum(status_counts.values())
    dlq_count = status_counts.get(JobStatus.FAILED.value, 0)

    return JobStatsResponse(
        total_jobs=total_jobs,
        status_counts=status_counts,
        dlq_count=dlq_count,
        queue_name=queue.name,
    )


@router.get("/dlq", response_model=List[JobRead])
async def list_dlq_jobs(session: AsyncSession = Depends(get_session)):
    statement = select(Job).where(Job.status == JobStatus.FAILED)
    result = await session.exec(statement)
    return result.all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/requeue", response_model=JobRead)
async def requeue_failed_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Only FAILED jobs can be requeued. Current status: {job.status}"
        )

    job.status = JobStatus.PENDING
    job.retry_count = 0
    job.error_message = None
    job.updated_at = datetime.now(timezone.utc)

    session.add(job)
    await session.commit()
    await session.refresh(job)

    await queue.enqueue(
        "process_pdf_render",
        payload={"id": job.id, "filename": job.filename}
    )
    return job
@router.websocket("/jobs/{job_id}/ws")
async def job_progress_websocket(websocket: WebSocket, job_id: int, session: AsyncSession = Depends(get_session)):
    await websocket.accept()
    
    # 1. Fetch current job state from DB
    job = await session.get(Job, job_id)

    if not job:
        await websocket.send_json({"error": f"Job {job_id} not found"})
        await websocket.close(code=4004)
        return

    # 2. Handle Late Subscribers (Job already finished)
    initial_payload = JobProgressUpdate(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        updated_at=job.updated_at
    ).model_dump_json()
    
    await websocket.send_text(initial_payload)

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        await websocket.close(code=1000)
        return

    # 3. Subscribe to Redis Pub/Sub for active updates
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