# v2 step 1: shared object storage

MinIO Community is archived and source-only. This local demo builds its pinned
`RELEASE.2025-10-15T17-29-55Z` source using `docker/minio.Dockerfile` (AGPL-3.0);
the first build downloads Go dependencies and takes longer. Review upstream
maintenance and licensing before redistributing or deploying it. Production
object storage selection remains open.

The default remains local storage. The opt-in MinIO stack lets the API and worker
exchange `input.pdf` and `pages.zip` without mounting the same filesystem.

## Start a fresh MinIO-backed demo

```bash
docker compose -f docker-compose.yml -f docker-compose.storage.yml up --build -d
```

This requires Docker Compose 2.24.4+ (`!reset` removes the API/worker shared-volume
mounts). Add `-f docker-compose.monitoring.yml` for monitoring. MinIO API is on
localhost:9000 and its console on localhost:9001. Local-only credentials are in
`.env.example`; change them before any deployment. Bucket provisioning is an
idempotent one-shot service. The bucket must remain private and unversioned.

**Existing v1 jobs:** do not switch an existing job database to S3. Its files stay
in the old local volume and are not migrated automatically. Keep using the base
Compose command for the existing demo. Start v2 on a separate machine/checkout
with fresh data, or first export anything you need and explicitly plan a storage
migration. Do not remove existing volumes merely to try this feature.

## Flow and failure behavior

1. API validates the upload into a temporary file and lets PostgreSQL arbitrate
   idempotency. Only the winning request uploads the input object.
2. Input PUT finishes before the database commit and durable dispatch. A storage
   failure returns 503 and rolls back the new row.
3. Worker downloads input into a private temporary directory, renders pages, and
   PUTs the completed ZIP before the job can become COMPLETED. Storage failures
   use the existing job retry/failure path. Temporary files are removed normally;
   SIGKILL can leave scratch in the killed container's disposable filesystem.
4. Download fetches the ZIP into a temporary file and removes it after the response.
   Missing objects return 404; storage outages return 503.
5. Deletion removes both known object keys before deleting the database row. The
   tombstone remains if storage fails, allowing idempotent cleanup retries.

The app does not create buckets during normal requests. AWS S3 can use the same
SDK interface, but production IAM, encryption and deployment have not been tested.
Do not change storage backend/bucket while jobs exist. Versioned buckets need
additional version cleanup; this implementation deletes current objects only.
A successful object PUT followed by a database rollback/crash can leave an orphan
object (there is no transaction spanning PostgreSQL and S3). Orphan reconciliation,
retention, multi-worker crash benchmarks and Kubernetes remain follow-up work.
Single PUTs avoid abandoned multipart uploads; artifacts must fit S3's single-PUT
size limit. API downloads use local disk space proportional to the ZIP size.

## Verification

The existing suite still exercises local storage. Real MinIO tests additionally
exercise upload → worker with a different local path → ZIP download → deletion,
output publication failure, upload rollback, and cleanup recovery after an outage.

For a disposable MinIO instance configured with `dtq-test` / `dtq-test-password`:

```bash
DTQ_TEST_S3_ENDPOINT=http://127.0.0.1:19000 poetry run pytest -q
```

Use an isolated test PostgreSQL database and Redis too: tests create and drop
schema tables. CI provisions all three services and runs these integration tests.
