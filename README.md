# Distributed PDF Task Queue

Upload a PDF, render its pages into PNG images in a background worker, and download the images as a ZIP archive. FastAPI and React provide submission, progress, job status, failed-job requeueing, and downloads. PostgreSQL stores job state; Redis-backed SAQ executes tasks and Redis Pub/Sub delivers WebSocket progress events.

## Run locally with Docker

```bash
docker compose up --build -d
cd frontend
npm install
npm run dev
```

Open http://localhost:3000, choose a PDF, and select **Upload and Render**. The job page polls status every second and offers a ZIP download when processing completes. API documentation is available at http://localhost:8000/docs.

The Docker image installs Poppler (`pdfinfo` and `pdftoppm`). API and worker containers share the `job_files` volume, so uploads and results survive container restarts. API processes run as a non-root user.

For running the API/worker outside Docker, install `poppler-utils`, install Python dependencies, and configure the individual PostgreSQL and Redis variables in `.env` for your local services. Set `STORAGE_DIR` to the same directory for both processes.

## Processing behavior

- Accepts PDFs up to 20 MiB and 50 pages by default; encrypted PDFs are unsupported.
- Renders each page with a maximum dimension of 1600 pixels. Progress advances as pages finish; 100% means the ZIP is ready.
- Rejects empty uploads, non-PDF headers, invalid extensions, and oversized uploads. Malformed PDFs fail during processing and use the normal retry/failed-job flow.
- `max_retries` is the maximum **total attempts**, including the first attempt. A value of 3 permits an initial attempt and two retries. Manual requeue resets this budget and progress.
- Uses job IDs for storage paths; original upload names are display metadata. Rendering subprocesses have a 30-second timeout each.
- A failed Redis progress notification is logged; it does not change the persisted task outcome. The polling UI can still retrieve the state.

## API

- `POST /api/v1/jobs/upload`: multipart fields `file`, optional `max_retries` and `idempotency_key`.
- `GET /api/v1/jobs/{id}`: job state and progress.
- `GET /api/v1/jobs/{id}/result`: ZIP download for a completed job.
- `GET /api/v1/jobs/{id}/ws`: WebSocket progress events.
- `GET /api/v1/jobs/stats`: job counts.
- `GET /api/v1/jobs/dlq`: failed jobs.
- `POST /api/v1/jobs/{id}/requeue`: retry a failed job using its saved input.

Filename-only JSON submissions are no longer supported. Jobs created before file uploads were added have no PDF input and must be submitted again through the upload form.

## Validation and remaining scope

Backend tests use real PDFs, Poppler, PostgreSQL, Redis, and SAQ, including upload-to-download, retry exhaustion, idempotent submission, and WebSocket delivery. Test fixtures create and drop tables: use a disposable test database. GitHub Actions provides isolated services and installs Poppler.

Remaining work includes authentication, automatic storage cleanup, reliable database-to-queue handoff, crash/timeout recovery, job listing/filtering, and frontend WebSocket reconnection. Shared disk storage supports multiple workers on one Docker host; workers on separate machines need shared/object storage. No Kubernetes deployment or throughput benchmark is implemented yet.
