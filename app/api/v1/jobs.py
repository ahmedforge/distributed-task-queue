from datetime import datetime, timezone
from typing import List
from pathlib import Path
import tempfile
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import col, func, select
from app.core.database import get_session
from app.core.queue import queue
from app.models.job import Job, JobRead, JobStatus
from app.schemas.job import JobProgressUpdate, JobStatsResponse
import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect
from app.core.config import settings
from app.core.storage import input_path, result_path, job_directory


router = APIRouter()


@router.post("", deprecated=True)
@router.post("/", include_in_schema=False)
async def create_job():
    raise HTTPException(
        400,
        "Filename-only submissions are no longer supported. Upload a PDF to /api/v1/jobs/upload.",
    )


@router.post("/upload", response_model=JobRead, status_code=201)
async def upload_job(
    file: UploadFile = File(...),
    max_retries: int = Form(3, ge=1),
    idempotency_key: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    filename = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not filename.lower().endswith(".pdf") or len(filename) > 255:
        raise HTTPException(
            400, "Choose a PDF file with a filename of at most 255 characters."
        )
    if idempotency_key:
        existing = (
            await session.exec(
                select(Job).where(Job.idempotency_key == idempotency_key)
            )
        ).first()
        if existing:
            return existing

    root = Path(settings.STORAGE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as output:
            temporary = Path(output.name)
            size = 0
            first = True
            while chunk := await file.read(1024 * 1024):
                if first and not chunk.startswith(b"%PDF-"):
                    raise HTTPException(
                        400, "The uploaded file does not have a PDF header."
                    )
                first = False
                size += len(chunk)
                if size > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "PDF exceeds the upload size limit.")
                await asyncio.to_thread(output.write, chunk)
            if size == 0:
                raise HTTPException(400, "The PDF file is empty.")

        job = Job(
            filename=filename, max_retries=max_retries, idempotency_key=idempotency_key
        )
        session.add(job)
        await session.flush()
        job_directory(job.id).mkdir(parents=True, exist_ok=True)
        temporary.replace(input_path(job.id))
        await session.commit()
        await session.refresh(job)
        try:
            await queue.enqueue(
                "process_pdf_render",
                payload={"id": job.id},
                retries=job.max_retries,
                timeout=1800,
            )
        except Exception:
            job.status = JobStatus.FAILED
            job.error_message = (
                "Queue unavailable. Requeue this job when Redis is available."
            )
            await session.commit()
            raise HTTPException(503, {"message": job.error_message, "job_id": job.id})
        return job
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        await file.close()


@router.get("/{job_id}/result")
async def download_result(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(409, "The job has not completed.")
    result = result_path(job_id)
    if not result.is_file():
        raise HTTPException(404, "The rendered result is unavailable.")
    return FileResponse(
        result, media_type="application/zip", filename=f"job-{job_id}-pages.zip"
    )


@router.get("/stats", response_model=JobStatsResponse)
async def get_job_stats(session: AsyncSession = Depends(get_session)):
    """Fetch aggregated job metrics across database and Redis queue."""
    # 1. Aggregate database counts by status
    statement = select(Job.status, func.count(col(Job.id))).group_by(Job.status)
    results = await session.exec(statement)
    raw_counts = dict(results.all())

    # Map counts across all defined statuses
    status_counts = {status.value: raw_counts.get(status, 0) for status in JobStatus}

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
            detail=f"Only FAILED jobs can be requeued. Current status: {job.status}",
        )

    job.status = JobStatus.PENDING
    job.retry_count = 0
    job.progress = 0
    job.error_message = None
    job.updated_at = datetime.now(timezone.utc)

    session.add(job)
    await session.commit()
    await session.refresh(job)

    await queue.enqueue(
        "process_pdf_render",
        payload={"id": job.id, "filename": job.filename},
        retries=job.max_retries,
        timeout=1800,
    )
    return job


@router.websocket("/{job_id}/ws")
async def job_progress_websocket(
    websocket: WebSocket,
    job_id: int,
    session: AsyncSession = Depends(get_session),
):
    await websocket.accept()

    # 1. Fetch job state
    job = await session.get(Job, job_id)

    if not job:
        await websocket.send_json({"error": f"Job {job_id} not found"})
        await websocket.close(code=4004)
        return

    # 2. Initial state payload
    initial_payload = JobProgressUpdate(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        updated_at=job.updated_at,
    ).model_dump_json()

    await websocket.send_text(initial_payload)

    # Late subscriber check
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        await websocket.close(code=1000)
        return

    # 3. Redis Pub/Sub subscription
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
