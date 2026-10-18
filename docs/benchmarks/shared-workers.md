# Shared-storage worker verification (v2)

Run from the repository root with Docker available:

```bash
poetry run python scripts/verify_shared_workers.py
```

The script builds the application, uses a local MinIO image (building the pinned
source if missing), and creates a uniquely named Docker network, PostgreSQL,
Redis, MinIO volume, API and two workers. It removes its containers, test volumes
and network in `finally`. Existing demo services and volumes are not used.
Failures are recorded in `shared-workers.json` and exit nonzero. This is a manual
integration experiment, not an automatically scheduled load test.

## What it checks

- API and both workers have no bind mounts or shared volumes. Only MinIO mounts
  the persistent test object-storage volume.
- Both worker processes complete jobs in a finite synthetic batch; completion
  logs identify which worker processed each job.
- A crash batch is queued while both workers are stopped. Only worker A starts.
  Once PostgreSQL reports PROCESSING, the script sends SIGKILL, checks exit 137,
  and removes A and its private scratch filesystem.
- Worker B alone resumes the queue. Every accepted job must complete, each ZIP
  must pass its CRC check and contain the expected PNG pages, and every confirmed
  interrupted job must record a retry.

## Limits

This runs separate containers on one laptop, not separate physical machines.
PostgreSQL, Redis and MinIO remain available, and their data is retained. It does
not establish exactly-once execution, full-host recovery, Redis-data-loss recovery,
linear scaling, or production throughput. The test changes only the queued crash
jobs to a 30-second timeout; the application default remains 1800 seconds. SAQ's
normal 60-second sweep remains enabled. Recovery includes waiting for that sweep.

Each worker is limited to 1 CPU / 1 GiB and one concurrent job. Normal application
concurrency remains 10 unless WORKER_CONCURRENCY is set. The small workload and
configuration differ from the original v1 benchmark, so the rates are not a
controlled before/after comparison.

For a **fresh MinIO-backed demo**, Compose now allows replicas:

```bash
docker compose -f docker-compose.yml -f docker-compose.storage.yml up --build -d --scale worker=2
```

Read [storage setup and migration limits](../shared-storage.md) first; do not
switch an existing local-storage job database without a migration plan.

## Recorded run

- Passed: True; raw evidence: [shared-workers.json](shared-workers.json).
- Measured batch: 12 synthetic two-page PDFs in 7.37s (1.63 jobs/sec), with 12 valid ZIPs.
- Participation: worker A completed 6 measured jobs; worker B completed 6.
- Crash: A was killed (exit 137) and removed while job [16] was PROCESSING.
- B alone completed all 4 crash-batch PDFs (50 pages each), with 4 valid ZIPs, in 83.20s after the kill.
- Retry counts: `{"16": 1, "17": 0, "18": 0, "19": 0}`. No accepted crash-batch jobs were unaccounted for in this run.

These results are correctness evidence for a small local experiment, not a throughput or availability guarantee. See the conditions above and the raw environment record.
