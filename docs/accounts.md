# Accounts and private jobs

Accounts are required for every job API and job WebSocket. Create an account at `/account`; usernames are case insensitive, 3–32 ASCII letters/digits/underscores, starting with a letter. Passwords must contain 12–128 characters. There is no email verification, password recovery, password-change UI, or admin web UI in this release. Save your password securely.

## Behavior

- A user sees only their job lists, failed jobs, counts, completion history, PDFs/ZIPs, retries, deletion controls, and live progress. Other owners' IDs return 404. Unauthenticated HTTP requests return 401; WebSockets reject unauthorized access with code 1008.
- The same idempotency key can be used independently by different accounts. PostgreSQL still arbitrates simultaneous submissions for the same account/key. Worker payloads contain job IDs, never browser credentials; dispatch, recovery, storage, and deletion retain their existing behavior.
- Broker-wide ready/active/delayed counts are operator data, so account dashboards omit them. They do not label database counts as broker measurements. `/metrics`, Grafana, and Prometheus remain operator interfaces and must be kept on a private network; this change does not add monitoring authentication.
- Login tokens are random and sent only as HttpOnly, host-only, SameSite=Lax cookies. Only SHA-256 token hashes are stored. Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 iterations; hashing runs off the event loop with a four-operation concurrency limit per API process. SQL bind parameters are hidden and SQL echo logging is disabled.
- Sessions expire after seven days. Signing in replaces that browser's old login; signing out revokes one login; signing out all devices revokes every login for that account. Existing live sockets recheck login validity at most every 15 seconds. Expired login rows are pruned when a new login is issued.
- Account/job responses use `Cache-Control: no-store`. Browser account changes clear job caches; other open tabs reload through BroadcastChannel. Tokens are not put in URLs or localStorage.
- Mutation requests require `X-DistribuQueue: 1`; supplied Origin headers must match the browser-facing origin. There is no cross-origin CORS allowance. WebSockets also validate Origin. API clients without an Origin header still need the custom mutation header and session cookie.
- Registration/login share atomic Redis limits: 10 attempts per username and 100 per client IP in a 15-minute window. Both successful and failed attempts count. If Redis is unavailable, account creation/login returns 503. Existing sessions and ordinary REST job reads can still authenticate through PostgreSQL. These are authentication limits, not PDF upload quotas or general traffic protection.

## Upgrade and existing jobs

Back up PostgreSQL and job storage before applying the new migration, then rebuild/restart the API, worker, and frontend together:

```bash
docker compose up --build -d
```

The existing API startup command runs `alembic upgrade head`. The migration adds account/session tables and nullable `job.owner_id`, and replaces global idempotency with owner-scoped uniqueness. Existing jobs and files remain intact, unowned and hidden from browser accounts. They are never automatically assigned to a new registrant.

For a personal installation where all old jobs belong to you, create your account first, then explicitly inspect and assign those unowned jobs from an operator terminal:

```bash
docker compose exec api python scripts/assign_legacy_jobs.py --username YOUR_USERNAME
# After reviewing the dry-run count:
docker compose exec api python scripts/assign_legacy_jobs.py --username YOUR_USERNAME --apply
```

This operator command assigns **all** unowned jobs. Do not use it on a shared installation unless the recipient owns every unowned job. It refuses conflicting idempotency keys and never transfers already-owned jobs. There is no browser endpoint for claiming old job IDs.

Downgrading removes accounts and owner associations. If multiple owners have reused one idempotency key, the downgrade refuses to run rather than silently clearing those keys. Back up and resolve such conflicts explicitly before rolling back. Do not expose an older unauthenticated API against shared private data.

## Local and HTTPS configuration

Local Compose/Vite uses HTTP with `AUTH_COOKIE_SECURE=false`. Before remote use, configure HTTPS, set `AUTH_COOKIE_SECURE=true` and `AUTH_ORIGIN` to the exact external origin (for example `https://queue.example.com`, no trailing slash). Compose forwards both settings; other launchers must pass them to the API environment. Browser and API paths must share the same origin. The development Vite proxy preserves Host for this check.

Configure forwarded headers only for a trusted reverse proxy and block direct backend access, so rate limits use the real client IP. Do not trust arbitrary public X-Forwarded-For input. A proxy which hides all clients behind one address shares the 100-attempt limit across those clients. This release does not configure cloud TLS, public upload quotas, storage retention, backups, or Redis-data-loss recovery.

## API clients

Use a cookie jar and the custom header. For example, put your own credentials in a local JSON file, restrict its permissions, and avoid committing it:

```bash
curl --fail-with-body -c /tmp/dtq-cookies -H 'Content-Type: application/json' -H 'X-DistribuQueue: 1' --data-binary @credentials.json http://localhost:8001/api/v1/account/login
curl --fail-with-body -b /tmp/dtq-cookies -H 'X-DistribuQueue: 1' -F 'file=@sample.pdf;type=application/pdf' http://localhost:8001/api/v1/jobs/upload
curl --fail-with-body -b /tmp/dtq-cookies http://localhost:8001/api/v1/jobs
```

Account endpoints under `/api/v1/account`: `POST /register`, `POST /login`, `GET /me`, `POST /logout`, `POST /logout-all`. The benchmark/shared-worker/KEDA scripts create temporary demo accounts so their requests follow the same authorization path. Prior recorded benchmark numbers are unchanged; the account-enabled version has not been rebenchmarked.

## Verification

`tests/test_accounts.py` exercises real auth without the test-only legacy override: password/token storage, cookie flags, owner isolation on all job routes and sockets, an actual PDF render/download, per-account concurrent idempotency, expiry/revocation, origin/custom-header checks, atomic rate limits, and explicit legacy-job assignment. Existing queue tests use a dependency override for their unowned fixtures; production has no anonymous bypass setting. The migration lifecycle check seeds a pre-account job and checks that upgrading preserves it.

Local verification for this update: 107 backend tests passed with real PostgreSQL, Redis and MinIO; the final account/metrics subset passed 24 tests after dashboard changes. Frontend lint/typecheck/build, legacy-data migration lifecycle, browser signup/upload/live completion/sign-out/sign-in, and a fresh-container account smoke test passed. GitHub-hosted CI and cloud deployment were not run as part of this update.
