# Version-tag image publishing

`.github/workflows/publish-images.yml` is separate from the existing PR/main CI.
It runs only on pushed `v*.*.*` tags; its metadata step accepts stable
`vMAJOR.MINOR.PATCH` versions (no leading zeroes or prereleases). It builds
Linux/amd64 images with buildx and GitHub Actions layer caching:

| Package | Build context / Dockerfile |
| --- | --- |
| `ghcr.io/ahmedforge/distributed-task-queue-api` | Repository root / `Dockerfile` |
| `ghcr.io/ahmedforge/distributed-task-queue-worker` | Same application build; worker command is in Kubernetes |
| `ghcr.io/ahmedforge/distributed-task-queue-frontend` | `frontend/` / `frontend/Dockerfile` |
| `ghcr.io/ahmedforge/distributed-task-queue-minio` | Repository root / `docker/minio.Dockerfile` |

API and worker intentionally share application contents and have distinct package
names. Bootstrap/migrations use the API package. PostgreSQL and Redis use their
upstream images. MinIO is our pinned AGPL-3.0 source build, not a new application.

Each package gets `vX.Y.Z` and `sha-abcdef0`. Only the numerically highest stable
version tag present in the repository also gets `latest`; publishing an older
version does not move it backward. Release runs are serialized to avoid concurrent
writes to `latest`. `scripts/image_tags.py` implements this policy and has
regression coverage for numeric ordering, old releases, and invalid tags.
`latest` depends on successful publication: a failed release is not a published
image. Existing version tags should not be moved or deleted.

The metadata job has `contents: read`; only the publish jobs add `packages: write`.
Login uses the job's `GITHUB_TOKEN`, with no personal token and no deployment
credentials. OCI source labels link packages to the repository. Package visibility
may need to be set to public manually in GitHub before anonymous local pulls work.
Existing packages must grant this repository Actions access if publication is
rejected. The workflow does not deploy anything.

## Security scan policy

The workflow runs Trivy on each locally loaded runtime image before publication,
filters to fixable HIGH/CRITICAL findings, prints the table, and retains it as an
artifact. The frontend scan is **blocking** (`exit-code: 1`). API, worker, and MinIO
findings are **report-only** (`exit-code: 0`) because the local scans found
existing fixable OS/upstream dependency vulnerabilities. Scanner execution
errors still fail the step in every service. These exceptions are not limited to OS packages: the API/worker also have
Starlette findings and MinIO has compiled Go dependencies. Framework and upstream
dependency upgrades remain necessary before treating these as deployable public
images. The measured local scan results and justification are
recorded in [verification](infrastructure-verification.md). This does not claim
that these images are free of vulnerabilities or ready for public deployment.

## Release procedure (owner runs these)

First commit/push the reviewed changes and let the existing CI pass. Choose an
unused stable version; `v1.0.0` below is only an example, not a published version:

```bash
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

After the tag workflow succeeds, inspect the four package pages and test a pull
of the selected version before choosing it in Terraform. No tag or image was
published during the local implementation checks. Workflow linting and local
build/scans do not prove that GitHub package permissions or remote pushes work.
