# Local v1 load and worker-crash measurements

Recorded October 15, 2026 (Asia/Karachi). This is one finite synthetic workload, not a production capacity or availability guarantee. [Raw measurements](latest.json) include the tested commit, app-source hash, Docker image ID, and individual job observations.

## Results

| Check | Observed result |
| --- | --- |
| Warm-up | 3 two-page PDFs, excluded from measured load |
| Load batch | 40/40 completed, 0 failed |
| Workload | 40 synthetic PDFs, 2 pages each, 760 bytes per input |
| Elapsed batch time | 21.78 seconds |
| Observed throughput | 1.84 completed jobs/second |
| Submission latency | p50 2.08s; p95 3.31s |
| Observed end-to-end latency | p50 10.92s; p95 12.51s |
| Load output checks | 40 ZIPs validated, 2 PNG pages each |
| Crash workload | 4 synthetic PDFs, 10 pages each |
| Worker failure | Actual SIGKILL, container exit 137; 4 jobs interrupted |
| Crash outcome | 4/4 completed, 0 failed, 0 unaccounted for |
| Recovery to final completion | 88.17 seconds after kill |
| Crash output checks | 4 ZIPs validated, 10 PNG pages each; every interrupted job recorded one failed/interrupted attempt |

## Environment and interpretation

- Host: 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz, 8 logical CPUs, 7.53 GiB RAM. Other user services stayed running.
- Docker limits: worker 2 CPUs / 1 GiB, API 1 CPU / 512 MiB, PostgreSQL 1 CPU / 512 MiB, Redis 0.5 CPU / 128 MiB.
- One SAQ worker, concurrency 10; client upload concurrency 8. Render output uses the application's 1600-pixel maximum page dimension.
- PDFs contain simple generated text, not scans, photographs, complex vectors, encrypted files, or realistic mixed-size documents.
- Batch time runs from starting submissions to observing every job as completed. Downloads/ZIP validation occur afterward and are excluded from throughput.
- Per-job latency starts when its upload acquires a client submission slot, excluding time waiting for that slot. Completion is observed by polling job lists every 200 ms, plus request time. These are observed client timings, not exact server completion timestamps.
- Crash test changes only its four queued SAQ task timeouts to 30 seconds before processing. The app default is 1800 seconds. The worker implementation and default 60-second sweep cadence are unchanged. The 88.17-second result does not predict default-config recovery time.
- Redis, PostgreSQL, and the shared file volume survived the worker kill. This does not test Redis data loss, expired queue records, multi-host storage, arbitrary repeated failures, or exactly-once execution.
- Every returned ZIP was checked for archive integrity, expected page count, and PNG signatures. Visual/pixel-perfect rendering was not scored.

## Reproduce

From the repository root, with Docker available and the project's Python environment installed:

```bash
poetry install
poetry run python scripts/benchmark.py --jobs 40 --concurrency 8
```

The script builds the current app, generates its own PDFs, and creates containers, a network, and storage with a unique `dtq-benchmark-` prefix. It publishes only an ephemeral localhost API port. It does not connect to the existing app's database, Redis, or PDF volume. The temporary services and volumes are removed in cleanup; the built image remains cached. Output defaults to `docs/benchmarks/latest.json`; use `--output` to retain a separate run. The table above records this run and must be refreshed manually if a new JSON result replaces it.

## Evidence-backed wording

> Built a Dockerized PDF task queue with durable dispatch, idempotent submissions, retry recovery, and live progress; measured 40 two-page jobs in 21.78 seconds on a worker limited to two CPUs, and recovered all four interrupted jobs in a controlled worker SIGKILL test.

Use the workload qualifiers when presenting these numbers. Do not describe this as a sustained service limit or a guarantee of zero job loss.

## v2: shared-storage workers

See the [separate two-worker and survivor-only crash experiment](shared-workers.md). Its workload and concurrency differ from this v1 run; the rates are not a controlled scaling comparison.
