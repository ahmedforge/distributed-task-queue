from datetime import datetime, timezone, timedelta
from typing import List
from pathlib import Path
import tempfile
import asyncio
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    UploadFile,
    File,
    Form,
    Query,
)
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from app.deletion import cleanup_job
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import col, func, select
from uuid import uuid4
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.dispatch import dispatch_job
from app.core.database import get_session, async_session_maker
from app.core.queue import queue, queue_counts
from app.models.job import Job, JobRead, JobStatus
from app.schemas.job import JobProgressUpdate, JobStatsResponse, JobListResponse
import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from app.core.config import settings
from app.core.storage import get_storage


router = APIRouter()


@router.get("", response_model=JobListResponse)
@router.get("/", response_model=JobListResponse, include_in_schema=False)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    job_status: JobStatus | None = Query(None, alias="status"),
    session: AsyncSession = Depends(get_session),
):
    statement = select(Job).where(Job.delete_pending == False)
    count_statement = select(func.count(col(Job.id))).where(Job.delete_pending == False)
    if job_status is not None:
        statement = statement.where(Job.status == job_status)
        count_statement = count_statement.where(Job.status == job_status)
    total = (await session.exec(count_statement)).one()
    statement = (
        statement.order_by(col(Job.created_at).desc(), col(Job.id).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    jobs = (await session.exec(statement)).all()
    return JobListResponse(items=jobs, total=total, page=page, page_size=page_size)


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
            if existing.delete_pending:
                raise HTTPException(
                    409, "The previous job with this idempotency key is being deleted."
                )
            return existing

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as output:
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
            filename=filename,
            max_retries=max_retries,
            idempotency_key=idempotency_key,
            dispatch_key=uuid4().hex,
            dispatch_pending=True,
        )
        # PostgreSQL arbitrates simultaneous submissions. A losing request
        # waits for the winner to commit, then returns its saved job without
        # writing input files or dispatching another task.
        statement = (
            pg_insert(Job)
            .values(**job.model_dump(exclude={"id"}))
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Job.id)
        )
        inserted_id = (await session.exec(statement)).scalar_one_or_none()
        if inserted_id is None:
            existing = (
                await session.exec(
                    select(Job).where(Job.idempotency_key == idempotency_key)
                )
            ).one_or_none()
            if existing is None or existing.delete_pending:
                raise HTTPException(
                    409,
                    "The job for this idempotency key is being deleted. Retry the upload after deletion finishes.",
                )
            return existing
        job = await session.get(Job, inserted_id)
        try:
            await asyncio.to_thread(get_storage().put, job.id, "input.pdf", temporary)
        except Exception:
            await session.rollback()
            raise HTTPException(
                503, "Upload storage is temporarily unavailable. Retry the upload."
            )
        await session.commit()
        await session.refresh(job)
        await dispatch_job(job.id, queue)
        return job
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        await file.close()


@router.get("/{job_id}/result")
async def download_result(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job or job.delete_pending:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(409, "The job has not completed.")
    with tempfile.NamedTemporaryFile(delete=False) as output:
        result = Path(output.name)
    try:
        await asyncio.to_thread(get_storage().fetch, job_id, "pages.zip", result)
    except FileNotFoundError:
        result.unlink(missing_ok=True)
        raise HTTPException(404, "The rendered result is unavailable.")
    except Exception:
        result.unlink(missing_ok=True)
        raise HTTPException(503, "Result storage is temporarily unavailable.")
    return FileResponse(
        result,
        media_type="application/zip",
        filename=f"job-{job_id}-pages.zip",
        background=BackgroundTask(result.unlink, missing_ok=True),
    )


@router.get("/stats", response_model=JobStatsResponse)
async def get_job_stats(session: AsyncSession = Depends(get_session)):
    """Fetch aggregated job metrics across database and Redis queue."""
    # 1. Aggregate database counts by status
    statement = (
        select(Job.status, func.count(col(Job.id)))
        .where(Job.delete_pending == False)
        .group_by(Job.status)
    )
    results = await session.exec(statement)
    raw_counts = dict(results.all())

    # Map counts across all defined statuses
    status_counts = {status.value: raw_counts.get(status, 0) for status in JobStatus}

    total_jobs = sum(status_counts.values())
    dlq_count = status_counts.get(JobStatus.FAILED.value, 0)

    sampled_at = datetime.now(timezone.utc)
    first_minute = sampled_at.replace(second=0, microsecond=0) - timedelta(minutes=14)
    minute = func.date_trunc("minute", Job.updated_at)
    completion_rows = await session.exec(
        select(minute, func.count(col(Job.id)))
        .where(
            Job.status == JobStatus.COMPLETED,
            Job.updated_at >= first_minute,
            Job.delete_pending == False,
        )
        .group_by(minute)
    )
    completions = dict(completion_rows.all())
    throughput = [
        {
            "minute": first_minute + timedelta(minutes=index),
            "completed": completions.get(first_minute + timedelta(minutes=index), 0),
        }
        for index in range(15)
    ]
    try:
        broker_counts = await asyncio.wait_for(queue_counts(queue), timeout=2)
        queue_error = None
    except (RedisError, OSError, TimeoutError):
        broker_counts = None
        queue_error = (
            "Redis queue metrics are unavailable. Document counts remain available."
        )

    return JobStatsResponse(
        total_jobs=total_jobs,
        status_counts=status_counts,
        dlq_count=dlq_count,
        queue_name=queue.name,
        sampled_at=sampled_at,
        queue_counts=broker_counts,
        queue_depth=(
            (broker_counts["queued"] + broker_counts["scheduled"])
            if broker_counts
            else None
        ),
        queue_error=queue_error,
        completed_per_minute=throughput,
    )


@router.get("/dlq", response_model=List[JobRead])
async def list_dlq_jobs(session: AsyncSession = Depends(get_session)):
    statement = select(Job).where(
        Job.status == JobStatus.FAILED, Job.delete_pending == False
    )
    result = await session.exec(statement)
    return result.all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job or job.delete_pending:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/{job_id}")
async def delete_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = (
        await session.exec(select(Job).where(Job.id == job_id).with_for_update())
    ).one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise HTTPException(409, "Only completed or failed jobs can be deleted.")
    job.delete_pending = True
    job.dispatch_pending = False
    session.add(job)
    await session.commit()
    cleaned = await cleanup_job(job_id)
    return JSONResponse(
        status_code=200 if cleaned else 202,
        content={"job_id": job_id, "cleanup_pending": not cleaned},
    )


@router.post("/{job_id}/requeue", response_model=JobRead)
async def requeue_failed_job(job_id: int, session: AsyncSession = Depends(get_session)):
    # Serialize requeue claims until the FAILED -> PENDING change is committed.
    job = (
        await session.exec(select(Job).where(Job.id == job_id).with_for_update())
    ).one_or_none()
    if not job or job.delete_pending:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Only FAILED jobs can be requeued. Current status: {job.status}",
        )

    job.dispatch_key = uuid4().hex
    job.dispatch_pending = True
    job.status = JobStatus.PENDING
    job.retry_count = 0
    job.progress = 0
    job.error_message = None
    job.updated_at = datetime.now(timezone.utc)

    session.add(job)
    await session.commit()
    await session.refresh(job)

    await dispatch_job(job.id, queue)
    return job


@router.websocket("/{job_id}/ws")
async def job_progress_websocket(websocket: WebSocket, job_id: int):
    await websocket.accept()
    redis_client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    pubsub = redis_client.pubsub()
    channel_name = f"job_progress:{job_id}"
    receive_task = message_task = None
    close_code = 1000
    try:
        # Subscribe first, so updates during the snapshot query remain buffered.
        await pubsub.subscribe(channel_name)
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
        if not job or job.delete_pending:
            await websocket.send_json({"error": f"Job {job_id} not found"})
            close_code = 4004
            return

        initial = JobProgressUpdate(
            job_id=job.id,
            status=job.status,
            progress=job.progress,
            updated_at=job.updated_at,
        )
        await websocket.send_text(initial.model_dump_json())
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            return

        last_updated = job.updated_at
        receive_task = asyncio.create_task(websocket.receive())
        message_task = asyncio.create_task(
            pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1,
            )
        )
        while True:
            done, _ = await asyncio.wait(
                [receive_task, message_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                if receive_task.result()["type"] == "websocket.disconnect":
                    return
                receive_task = asyncio.create_task(websocket.receive())
            if message_task in done:
                message = message_task.result()
                message_task = asyncio.create_task(
                    pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1,
                    )
                )
                if not message or message["type"] != "message":
                    continue
                update = JobProgressUpdate.model_validate_json(message["data"])
                if update.updated_at < last_updated:
                    continue
                last_updated = update.updated_at
                await websocket.send_text(update.model_dump_json())
                if update.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    return
    except WebSocketDisconnect:
        pass
    except (RedisError, OSError):
        # Clients reconnect and use REST polling while live updates are unavailable.
        close_code = 1013
    finally:
        tasks = [task for task in (receive_task, message_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await pubsub.aclose()
        finally:
            await redis_client.aclose()
            try:
                await websocket.close(code=close_code)
            except (RuntimeError, WebSocketDisconnect):
                pass
