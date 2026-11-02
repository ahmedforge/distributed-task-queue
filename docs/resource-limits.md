# Account budgets and file expiry

New uploads are admitted against per-account active-job, saved-job, and artifact-storage budgets. Concurrent API processes serialize admission on the account's PostgreSQL row. Requeue uses the same account lock before the job lock. Existing authentication and owner checks apply to `/api/v1/jobs/usage` as well as uploads and retries.

## Defaults and settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `ACCOUNT_MAX_ACTIVE_JOBS` | 5 | Pending plus processing jobs, including delayed retries and pending dispatch |
| `ACCOUNT_MAX_STORED_JOBS` | 50 | All retained job rows, including expired jobs and pending cleanup |
| `ACCOUNT_MAX_STORAGE_BYTES` | 536870912 (512 MiB) | Input PDFs, recorded ZIP sizes, and outstanding ZIP reservations |
| `MAX_RESULT_BYTES` | 104857600 (100 MiB) | Maximum ZIP size reserved and enforced for each new job |
| `JOB_RETENTION_DAYS` | 7 | New job policy: delete job and files this many days after terminal completion/failure; 0 disables expiry for new uploads |

Compose forwards these settings to the API. Other deployment methods must provide them to the API environment. Each job snapshots its ZIP cap and retention policy when uploaded; workers enforce the saved ZIP cap, and terminal progress uses the saved retention policy. Changes affect new uploads, not existing jobs. Account budgets apply to subsequent admission immediately, but do not cancel or delete previously admitted jobs.

An upload needs room for its PDF plus the full result reservation. A successful render replaces the reservation with the actual ZIP size. A failed or interrupted render conservatively retains its reservation because an output write could have succeeded without recording completion. Deleting the job releases space only after file removal and database deletion commit. Operators can also measure finished jobs to replace conservative reservations with observed sizes. Quotas concern retained input/ZIP artifacts; worker scratch, staging/orphan files after a hard crash, object versions, database overhead, and accounts created separately are not a hard volume-capacity guarantee. Configure host/service limits separately for a public service.

The renderer stops writing its ZIP at the saved cap and never publishes an oversized ZIP. This is a compressed ZIP cap, not an input-page image or temporary-space cap. A retry uses the same saved cap. Storage errors and cancelled attempts retain their budget until a later successful render, explicit measurement, or cleanup.

Active admission rejection returns HTTP 429 with `Retry-After: 30`; waiting for a slot can take longer than 30 seconds. Saved-job/storage exhaustion returns 409 with a concrete cleanup message. Returning an existing idempotent job does not require another quota slot, even when admission is full. An expiring or deletion-pending idempotency claim cannot be reused until cleanup finishes. Quotas do not replace request-rate limits, account signup protections, or global infrastructure limits.

## Automatic expiry

New uploads record the chosen retention duration. After completion or terminal failure, `expires_at` is saved in the same transaction as the job's final state. Retries clear the old deadline and start a fresh deadline when that retry finishes, using the original saved duration. Active jobs are never expired by the cleanup pass.

Expired jobs disappear from job lists, details, downloads and failed-job results at their deadline, even if the cleanup loop is down. The API's existing background loop claims up to 100 expired finished jobs per pass using row locks and `SKIP LOCKED`, commits durable deletion markers, and then uses the existing cleanup system. It removes input PDFs, ZIPs and leftover job scratch before deleting metadata. A storage failure preserves the marker and quota charge; future passes retry it, including after restart. Multiple API processes cannot race a retry past an expiry/deletion claim. The deadline is checked under the same job lock before requeue.

The API must remain running for automatic cleanup. Recovery/dispatch delays and storage outages can delay physical deletion. Downloads already copied to a temporary response file can finish while the saved job is deleted. Once cleanup completes, the old idempotency key becomes available again. Expiry permanently deletes the job and its files; download results you want to keep. Job details show the exact deadline, and upload screens show the policy before submission.

## Upgrading without deleting old files

Back up PostgreSQL and job storage, then rebuild the API, workers, and frontend together:

```bash
docker compose up --build -d
```

The startup migration adds accounting fields and expiry metadata. **Existing jobs keep `retention_days=NULL` and `expires_at=NULL`; the upgrade does not schedule them for automatic deletion.** Existing sizes are unknown rather than invented as zero. Accounts with unmeasured old files cannot submit or retry jobs until an operator measures them, or the user deletes finished jobs and cleanup completes. Existing downloads remain available.

Inspect finished jobs against the configured storage backend, then explicitly save the measurements:

```bash
docker compose exec api python scripts/measure_job_storage.py
# After reviewing the inspected count:
docker compose exec api python scripts/measure_job_storage.py --apply
```

This command never deletes files or changes expiry deadlines. It skips active jobs, handles missing artifacts as size zero, and leaves unknown fields unchanged on a storage outage. It processes each job in its own transaction, so a later failure can leave earlier jobs measured; rerunning is safe. Use it again after old active jobs finish. It shares account/job lock ordering with admission and requeue. Always point it at the same local volume or MinIO bucket used by the application. A failed job whose input file is missing can be measured but still cannot render successfully without a new upload.

For pre-account jobs, use the separately documented [explicit owner assignment](accounts.md#upgrade-and-existing-jobs); then measure storage. Assignment does not silently make old jobs expire. A schema downgrade drops the new metadata but cannot restore already-deleted jobs or files.

## UI and verification

The dashboard shows active jobs, saved jobs, and used plus reserved storage. Uploads show the result reservation and retention policy. Cleanup-pending rows count against quota even though hidden from normal job lists. Unknown measurements are labelled explicitly. The usage response never lists other users' files.

Tests cover concurrent uploads and requeues, idempotent retries at capacity, independent owners, real PDF result sizes, oversize output rejection, durable cleanup outages, expiry/retention policy, disabled expiry, legacy preservation and accounting, and real MinIO size/expiry operations. The migration lifecycle verifies legacy sizes remain unknown and legacy deadlines remain disabled. Existing benchmark/shared-worker scripts use explicitly increased per-account budgets in their disposable stacks; prior recorded workload measurements remain unchanged and are not measurements of the new quota-enabled version. KEDA workload scripts use the deployed API's configured limits, so configure an appropriate test budget before reproducing a larger batch.

Local verification: 124 backend tests passed with real PostgreSQL, Redis and MinIO; frontend lint/typecheck/build and migration upgrade/rollback checks passed. Browser checks showed account budgets before upload and the exact expiry of an actual rendered PDF. A fresh Docker image passed admission, idempotency, rendering, reservation release, expiry and cleanup checks, and includes the measurement command. Cloud deployment, GitHub-hosted CI, and a new KEDA scaling experiment were not run.
