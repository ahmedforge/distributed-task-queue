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
unused stable version. `v2.0.0` is already published; `v2.0.1` below is an example for a future release, not a tag created by this guide:

```bash
git tag -l
git tag -a v2.0.1 -m "v2.0.1"
git push origin v2.0.1
```

After the tag workflow succeeds, inspect the four package pages and test a pull
of the selected version before choosing it in Terraform. The first release, [`v2.0.0`](https://github.com/ahmedforge/distributed-task-queue/actions/runs/39557477535), published all four images successfully on October 21, 2026. Anonymous pulls, OCI source labels, and matching stable/SHA/latest manifest digests were verified. The API job emitted a non-fatal Trivy cache-save warning; its scan and publication still passed. See [recorded release verification](infrastructure-verification.md#published-release-verification--october-21-2026).

If a release needs a code fix before any image publishes, a failed tag can be replaced after checking remote publication. Once any image has published, use a new patch version instead of force-moving that tag. A transient job can be rerun for the unchanged tagged commit. This workflow does not support release-candidate publishing: its metadata validation rejects prereleases before any image build, so they cannot update `latest`.
