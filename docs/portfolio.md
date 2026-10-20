# DistribuQueue portfolio and interview notes

## Resume bullets

Pick two or three, retaining the workload qualifiers on measured results:

- Built a Dockerized PDF processing system with FastAPI, PostgreSQL, Redis-backed SAQ, and React, providing live WebSocket progress, ZIP downloads, failed-job management, and Prometheus/Grafana monitoring.
- Implemented database-enforced concurrent-upload idempotency, durable dispatch intent, bounded retries with jitter, worker-state reconciliation, and retryable file deletion; validated backend behavior with 86 local tests and maintained GitHub Actions checks.
- Measured 40 synthetic two-page PDFs in 21.78 seconds (1.84 jobs/sec) on a worker limited to two CPUs; recovered all four interrupted jobs in a controlled SIGKILL test with a 30-second task timeout and Redis/storage retained.

- Added MinIO shared storage and Terraform/Helm provisioning for a local kind cluster; verified full destroy/recreate and KEDA 1 → 3 → 1 worker scaling while processing 16 synthetic twenty-page PDFs and validating all 320 output pages as PNG files.

Short project description:

> DistribuQueue is a local distributed-task-processing demo that separates PDF upload from background rendering and makes delivery, retry, progress, and cleanup behavior visible and testable.

## A 60-second walkthrough

[Watch the screenshot walkthrough](demo/walkthrough.mp4) or [open the GIF preview](demo/preview.gif). This is an edited sequence of genuine screenshots, not continuous screen recording. All document names and inputs were synthetic and ran on disposable services separate from personal documents.

| Time | What to explain |
| --- | --- |
| 0–5s | The app accepts PDFs and renders pages asynchronously. |
| 5–13s | Dashboard counts and queue/throughput charts show job state. |
| 13–21s | An optional idempotency key identifies repeated submissions. |
| 21–29s | Progress is persisted and delivered through Redis Pub/Sub and WebSockets. |
| 29–37s | Completion exposes a ZIP; terminal jobs can be deleted with confirmation. |
| 37–45s | Failed jobs retain useful errors and offer manual retry. A corrupt PDF must be corrected; retry alone does not repair it. |
| 45–55s | Grafana is provisioned with API and worker metrics. |
| 55–60s | Quote the measured workload and the controlled crash-test conditions. |

## Explain durable dispatch

The gap was between committing a PostgreSQL job and enqueueing it in Redis. A crash in that gap could leave a saved job with no task to run.

The job row now stores a unique dispatch key and `dispatch_pending=true` in the same PostgreSQL transaction as its job state. After commit, the API attempts delivery immediately. A background loop also checks pending delivery markers every two seconds, including after API restart. It locks each job row before enqueueing and clears the marker after acknowledgement. A temporary Redis failure leaves the committed marker available for another attempt.

Retries of delivery use the same SAQ key, which suppresses duplicates while that queue task is incomplete. A worker receipt also clears the marker before rendering, closing the case where Redis accepted the task but its response or the dispatcher was lost. Tasks from an older manual-requeue generation are ignored, and terminal jobs are not rendered again.

This is a recoverable handoff, not a distributed transaction or an exactly-once guarantee. Files are moved before the database commit; failure in that separate storage step can still leave an orphaned directory. Redis data loss after acknowledged delivery and queue records expiring during prolonged downtime are known limits.

## Explain the idempotency race

The original code first looked up the key, then inserted a job if it found nothing. Two simultaneous requests could both miss the lookup; the unique database constraint then rejected one insert.

The fix uses PostgreSQL `INSERT ... ON CONFLICT DO NOTHING ... RETURNING id`. Only the winning request writes the final PDF and dispatches a task. A losing request selects the winner's committed row and returns that same job. Its temporary upload is cleaned up. A concurrency test forces both requests past the initial lookup and verifies one job, one saved PDF, and one queue task. That test fails against the original implementation.

## Explain retries and worker interruption

`max_retries` means total attempts, including the first. Automatic retries use bounded exponential backoff with jitter. Manual requeue locks the failed job, resets its attempt budget, and assigns a new dispatch generation.

After an attempt, a worker hook reconciles SAQ's outcome with PostgreSQL. The API also reconciles interrupted jobs after SAQ sweeps a dead worker. A scheduled retry becomes `PENDING`; an exhausted/aborted attempt becomes `FAILED`. Queue state is refreshed under the row lock, preventing a stale snapshot from overwriting a newer active attempt. Later attempts preserve the count of attempts lost to a worker crash.

The default task timeout is 1800 seconds and SAQ's default sweep cadence is 60 seconds. Hard-kill recovery is not immediate. The recorded crash check deliberately used a 30-second timeout for its four queued test jobs; its 88.17-second recovery result must not be presented as the default configuration's recovery latency.

## Explain deletion

Only completed and failed jobs can be deleted. Deletion and requeue use the same row lock. The API commits a deletion marker before touching files, hides the job from normal views, removes the job directory, then removes the database row. If storage removal or the final database commit fails, the marker remains and cleanup retries after restart. This avoids exposing a retry action for a job whose input is being removed.

## Evidence and limits

- [Local Kubernetes scaling evidence](kubernetes.md#recorded-verification) and [shared-worker crash evidence](benchmarks/shared-workers.md) cover the bounded local v2 scope.
- [Recorded load and crash report](benchmarks/README.md), [raw observations](benchmarks/latest.json), and `scripts/benchmark.py` support the numeric claims.
- One finite batch of simple generated PDFs is not a sustained service-capacity benchmark.
- The crash check retained PostgreSQL, Redis, and shared storage; it did not test a full-host failure or Redis data loss.
- Default local storage is limited to one shared Docker host. The optional MinIO backend lets workers use independent filesystems; the verified Kubernetes demo still runs on one physical host.
- The app is a local portfolio demo, with no authentication, owner isolation, public-service rate limiting, or age-based retention policy.
- Shared object storage, two-worker crash recovery, and local Kubernetes/KEDA scaling are implemented in v2. Terraform now provisions local kind only; cloud rollout and multi-node availability remain outside scope. No AWS deployment is claimed.

## v1 completion record

The agreed local v1 scope is complete: application flows, CI checks, monitoring, idempotency race coverage, recorded load/SIGKILL measurements, screenshots, a short walkthrough, and evidence-backed presentation material. Commit and push actions remain with the project owner. The bounded local v2 adds shared storage, independent-worker recovery, and Kubernetes queue-driven scaling; further infrastructure features require a separate scope.
