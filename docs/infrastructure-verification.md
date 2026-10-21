# Infrastructure verification — October 20, 2026

Terraform provisions a local kind cluster. Not deployed to a cloud provider.

The October 20 commands were run against the working tree based on `c9df5b0`; the new
changes were not committed or pushed. This records **local verification**, not a
GitHub Actions run or a successful GHCR publication. Temporary tools were placed
under `/tmp/dtq-infra-tools`; commands below shorten that prefix to the executable
name. No default Kubernetes context or cloud account was selected.

## Tools and dependency setup

- Terraform 1.16.6; kind CLI 0.33.0; kubectl from the pinned 1.35.8 node.
- Providers initialized successfully: `tehcyx/kind` 0.11.0, `hashicorp/helm` 3.3.0,
  `hashicorp/kubernetes` 3.4.0. Both generated lock files are included for commit.
- Kubernetes 1.35.8, pinned existing node image digest; KEDA Helm chart 2.21.0.
- Trivy 0.76.0 and actionlint 1.7.12. Poetry 2.5.1 was installed in a temporary
  environment because the existing user launcher points to a removed installation.

`terraform -chdir=terraform/cluster init` and the corresponding `app` command
both reported:

```text
Terraform has been successfully initialized!
```

## Formatting, validation and existing CI checks

The final `terraform fmt -check -recursive terraform` exited 0. Both
`terraform -chdir=terraform/STAGE validate -no-color` commands returned:

```text
Success! The configuration is valid.
```

Actual local checks:

| Command/check | Result |
| --- | --- |
| `actionlint` | Exit 0 for both workflows |
| `bash -n scripts/k8s-local.sh scripts/kind-load-images.sh` | Exit 0 |
| `.venv/bin/black --check app worker tests alembic scripts` | 46 files unchanged |
| Poetry 2.5.1 `check --lock` / `install --no-interaction` | Passed; no dependency changes |
| Migration lifecycle on a disposable database | Passed |
| `.venv/bin/python -m pytest -q` | 86 passed, 2 warnings in 68.89s (0:01:08) |
| `npm --prefix frontend ci` | Passed |
| `npm --prefix frontend run lint` | Passed |
| `npm --prefix frontend run build` | TypeScript and Vite build passed |
| `git diff --check` | Exit 0 |

Backend checks used separate disposable PostgreSQL, Redis, real MinIO and PDF
rendering. Test containers were removed afterward. The warnings are existing
Starlette deprecations. Poetry emits metadata deprecations and Vite warns about
its future native config loader; these checks still exited 0.

The PR/main CI workflow was not changed. Six new release-policy cases account
for the change from 80 to 86 tests. The new tag workflow was linted. Local image builds and cached runtime-image
scans were checked separately; its remote push permissions remain unverified.

## Terraform lifecycle: actual output

The initial `kind get clusters` returned `No kind clusters found.` The first
cluster plan/apply created two resources. The initial app plan proposed seven.
Slow registry downloads then caused the first KEDA install to hit its original
600-second timeout. That failed create left a Helm release outside state; a direct
retry reported `cannot re-use a name that is still in use`. The actual recovery was:

```bash
terraform -chdir=terraform/app import helm_release.keda keda/keda
terraform -chdir=terraform/app plan -out=app.tfplan
terraform -chdir=terraform/app apply app.tfplan
```

The import succeeded and the resumed apply completed. The final configuration
uses a 1200-second KEDA timeout and requests atomic cleanup on install failure.
The failure path for that new atomic setting was not injected; its fresh install
was verified during the next lifecycle.

After the first complete deployment and workload check, the following commands
ran successfully, in order:

```bash
terraform -chdir=terraform/app plan -destroy -out=destroy.tfplan
terraform -chdir=terraform/app apply destroy.tfplan
terraform -chdir=terraform/cluster plan -destroy -out=destroy.tfplan
terraform -chdir=terraform/cluster apply destroy.tfplan
kind get clusters
terraform -chdir=terraform/cluster plan -out=cluster.tfplan
terraform -chdir=terraform/cluster apply cluster.tfplan
./scripts/kind-load-images.sh dtq-tf
terraform -chdir=terraform/app plan -out=app.tfplan
terraform -chdir=terraform/app apply app.tfplan
```

Recorded output excerpts:

```text
# app destroy
Apply complete! Resources: 0 added, 0 changed, 7 destroyed.
# cluster destroy
Apply complete! Resources: 0 added, 0 changed, 2 destroyed.
# kind get clusters
No kind clusters found.
# fresh cluster plan and apply
Plan: 2 to add, 0 to change, 0 to destroy.
Apply complete! Resources: 2 added, 0 changed, 0 destroyed.
# fresh app plan and apply
Plan: 7 to add, 0 to change, 0 to destroy.
Apply complete! Resources: 7 added, 0 changed, 0 destroyed.
```

Both final `plan -detailed-exitcode` commands exited 0 and reported:

```text
No changes. Your infrastructure matches the configuration.
```

The Docker node ID changed from `0e72a8d58b6b` to
`155fbe126fff`. The second cycle used cached application and
exact official KEDA images, with a fresh node and PVCs; it is not a cold-network
timing benchmark. Kind discards unpacked compressed layers, so direct export of
its image cache failed. The official KEDA blobs were downloaded and their OCI
digests verified before importing them to Docker for the shared loader.

On the recreated stack:

```text
GET http://127.0.0.1:18001/health -> HTTP 200, {"status":"healthy"}
GET http://127.0.0.1:3002/       -> HTTP 200
scaledobject.keda.sh/worker-queue condition met
```

An additional synthetic two-page PDF was uploaded on that new stack; job 1
completed and its downloaded ZIP passed integrity, two-page count, and PNG
signature checks. The demo is left running for review. Use the documented
app-first, cluster-second destroy commands when finished.

## Real queue scaling

[Raw Terraform-cluster observations](benchmarks/terraform-keda.json) record
16 synthetic twenty-page PDFs, four concurrent submissions, and one job slot
per worker. The actual worker count moved **1 → 3 → 1**. All 16 ZIPs passed archive,
page-count and PNG signature checks (320 PNG pages); a completed WebSocket
snapshot through the frontend proxy also passed. Runtime container counts were
exactly one at both ends, with no retiring worker pods remaining.

The first three-ready-worker observation was at 14.15 seconds;
the final one-pod observation was at 197.33 seconds. These are observations
from this finite correctness experiment, not a throughput improvement claim.
The node was limited to 2 CPUs / 3 GiB RAM / 4 GiB RAM+swap on a shared laptop;
image caching overlapped with the first workload. No multi-host or cloud
availability was tested. Existing benchmark numbers remain unchanged.

## Image variables and publishing boundary

A separate, **unapplied** plan with `use_local_images=false` and example
`image_tag=v1.0.0` selected all four expected references:

```text
ghcr.io/ahmedforge/distributed-task-queue-api:v1.0.0
ghcr.io/ahmedforge/distributed-task-queue-frontend:v1.0.0
ghcr.io/ahmedforge/distributed-task-queue-minio:v1.0.0
ghcr.io/ahmedforge/distributed-task-queue-worker:v1.0.0
```

This verifies template substitution only. No package was pulled from those
example tags, and no Git tag, commit, push, workflow dispatch, or package
publication was performed. The owner's first version-tag run must verify GHCR
permissions, package visibility and actual pushes.

## Vulnerability policy and actual strict-gate exits

The [scan snapshot](infrastructure-scans.json) records the image IDs,
filter, official DB digest, and each finding. Counts are **package vulnerability
occurrences**, not unique CVEs, for the local cached runtime images on this date.
They do not predict the contents of a future fresh release build.

| Image | Fixable HIGH | Fixable CRITICAL | OS / language findings | Strict scan exit | Workflow policy |
| --- | ---: | ---: | --- | ---: | --- |
| API | 11 | 0 | 8 / 3 | 1 | Report-only |
| Worker | 11 | 0 | 8 / 3 | 1 | Report-only |
| Frontend | 0 | 0 | 0 / 0 | 0 | Blocking |
| MinIO | 71 | 5 | 10 / 66 | 1 | Report-only |

The strict command was actually run on each image:

```bash
trivy --cache-dir /tmp/dtq-infra-tools/trivy-cache image \
  --skip-db-update --timeout 20m --no-progress --scanners vuln \
  --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1 IMAGE
```

API/worker include Debian curl/expat findings and three Starlette advisories;
MinIO includes Alpine packages, its pinned Go toolchain, and upstream Go
libraries. These are not all outside the project's control. Framework/base-image
and upstream dependency maintenance remain necessary; the exceptions deliberately
allow publication for a local demo and do not constitute security clearance.
The frontend is blocking because its measured strict scan passed. Scanner errors
fail every service's workflow step even when vulnerability findings are report-only.

The default database download first timed out and a GHCR retry failed with an
HTTP/2 protocol error. A resumable download of the official artifact succeeded,
with its SHA256 checked against the OCI manifest before Trivy used it:

```text
sha256:75ec5870d5330e6da8d60bcd7f155ed8898729376a4104a957d03360086b9a43
```

## Scope and local files

No automatic deployment, auth/ownership, AWS resources, or cloud rollout was
implemented. Monitoring remains in Compose, matching the existing Kubernetes
scope. State, plans, tfvars and kubeconfig are ignored and contain no tracked
new credentials; their local file permissions were restricted to the owner.
`umask 077` is included in the setup instructions for future commands. The root
Docker build context excludes `terraform/` as well, keeping its state, secrets
and downloaded providers out of image build inputs.


## Follow-up verification — October 21, 2026

The final MinIO Dockerfile build was rerun successfully:

```bash
docker build --progress=plain -f docker/minio.Dockerfile -t dtq-minio:infra-verified .
docker run --rm --network none dtq-minio:infra-verified --version
```

The Go install and final image layers were cached on this successful invocation;
this is not a timed cold build. Earlier module downloads had stalled. The only
Dockerfile change sets `GODEBUG=http2client=0` in the Go builder stage to use HTTP/1
for module downloads; it does not change TLS verification or the final runtime
environment. The successful executable check printed:

```text
minio version DEVELOPMENT.GOGET (commit-id=DEVELOPMENT.GOGET)
Runtime: go1.24.8 linux/amd64
License: GNU AGPLv3 - https://www.gnu.org/licenses/agpl-3.0.html
```

This image was not substituted into the earlier vulnerability snapshot or
Terraform workload, which used the image IDs recorded in their reports. The
release workflow will scan its own build before publishing it.

The existing local demo was checked again: API health returned
`{"status":"healthy"}` and the frontend returned HTTP 200. The release-tag
helper's CLI was also checked with a deliberately different `GITHUB_SHA`; its
SHA tag used `git rev-parse HEAD`, so annotated Git tags resolve to the commit.
No release tag was created or pushed.


## Published release verification — October 21, 2026

These follow-up checks use committed release `v2.0.0` at `d70ebb729b297c29d5f4d134fd9af28e2eb48970`; the earlier local-only publication limits above describe the October 20 checks.

`git tag -l` initially returned no tags. Main CI had passed for this commit and the working tree was clean. The following annotated tag commands were actually run, with the user's authorization:

```bash
git tag -a v2.0.0 -m "v2.0.0"
git push origin v2.0.0
```

[Publishing workflow](https://github.com/ahmedforge/distributed-task-queue/actions/runs/39557477535): **success**, including all four image jobs. The API scan emitted a non-fatal cache-save warning. No release tag was moved, no release candidate was published, and no new source commit was made by the agent.

After `docker logout ghcr.io`, all four `docker pull ghcr.io/ahmedforge/distributed-task-queue-SERVICE:v2.0.0` checks succeeded using a new empty temporary Docker config. This proved anonymous access without adding an imagePullSecret or modifying package visibility. The OCI `org.opencontainers.image.source` label in every image was `https://github.com/ahmedforge/distributed-task-queue`.

Anonymous registry manifest requests confirmed that each image's `v2.0.0`, `sha-d70ebb7` and `latest` tags matched:

| Service | Published manifest digest |
| --- | --- |
| api | `sha256:00952a43c6981b2ad77324d570185c7f2dd31d68053bd2d70ea1c10a20af724a` |
| worker | `sha256:a4349620e3e0cc273a3aa62ad7ed1d16174ccc2795d24f65388b7fe55774deb8` |
| frontend | `sha256:5e523200492a857e648f19de6812e358e39060ae7fe9ab31ec3265b0e0732e75` |
| minio | `sha256:aa07c32b36e18263ad9a394e3fdacabfa8dc1c432617d3566ad4d8d55ef01f8a` |

Actual release Trivy reports (fixable HIGH/CRITICAL only):

| Image | HIGH | CRITICAL | Policy |
| --- | ---: | ---: | --- |
| API | 3 | 0 | Report-only |
| Worker | 3 | 0 | Report-only |
| Frontend | 0 | 0 | Blocking |
| MinIO | 62 | 5 | Report-only |

These are package vulnerability occurrences, not unique CVEs. API/worker findings are Starlette advisories; MinIO findings are in the compiled Go toolchain/dependencies. OS findings were zero in these fresh release images. The existing report-only exceptions include dependencies the project can upgrade; successful publication is not security clearance. The older cached-image snapshot remains unchanged as historical evidence. The release's Trivy text reports were downloaded separately from Docker build-record artifacts, which the GitHub CLI could not extract as ZIP files.


### Published images on a fresh kind node

Preparation included a teardown-order mistake: cluster removal started before the old namespace deletion had finished, so the old app destroy failed with a connection error. Docker confirmed the old node was gone. The now-nonexistent namespace state entry was removed with `terraform -chdir=terraform/app state rm kubernetes_namespace_v1.app`. The kind provider could not refresh a deleted cluster (`could not locate any control plane nodes`), so its stale entry was removed with `terraform -chdir=terraform/cluster state rm kind_cluster.local`. This repair was limited to the confirmed-deleted disposable demo. The final teardown was subsequently guarded to run cluster destruction only after a successful app destroy.

The old `dtq-app:k8s`, `dtq-frontend:k8s`, and `dtq-minio:k8s` tags were removed. A new Terraform-managed node was created with Docker ID `1d0a2f20aef625e3c8fcf92ec41391dda8a13a5a9f7c90eb130ec473c3d5e697`. No application image was imported into this node. Host Docker anonymous pulls and the fresh kind node use separate image stores; pod events recorded registry pulls, including the API image first pulled by the migration/bucket bootstrap hook.

Actual commands from the repository root (temporary binary paths are omitted in this abbreviated transcript):

```bash
terraform -chdir=terraform/cluster plan -out=release-cluster.tfplan
terraform -chdir=terraform/cluster apply release-cluster.tfplan
terraform -chdir=terraform/app plan -var=use_local_images=false -var=image_tag=v2.0.0 -out=release-app.tfplan
terraform -chdir=terraform/app apply release-app.tfplan
kubectl --kubeconfig terraform/cluster/kubeconfig --context kind-dtq-tf -n dtq get pods -o jsonpath='{..image}'
kubectl --kubeconfig terraform/cluster/kubeconfig --context kind-dtq-tf -n dtq describe pods
kubectl --kubeconfig terraform/cluster/kubeconfig --context kind-dtq-tf -n dtq wait scaledobject/worker-queue --for=condition=Ready --timeout=120s
```

Actual results:

```text
# cluster apply
Apply complete! Resources: 2 added, 0 changed, 0 destroyed.
# app apply
Apply complete! Resources: 7 added, 0 changed, 0 destroyed.
# smoke check
API health: {"status":"healthy"}
Frontend HTTP 200
Job 1: COMPLETED
ZIP integrity, two-page count and PNG signatures: passed
```

All four application containers were Ready, selected the expected `ghcr.io/ahmedforge/distributed-task-queue-SERVICE:v2.0.0` reference, had no imagePullSecrets, and reported runtime image digests identical to the anonymous manifest checks above. PostgreSQL, Redis, and KEDA retained their upstream images. Cold pulls took several minutes; no application images were locally loaded to bypass them. KEDA's initial certificate refresh restarted its operator once before it became Ready. This was a deployment/smoke check, not a repeated load or scaling benchmark.


### Final release-test teardown

The following commands ran sequentially under `set -e`, so cluster deletion could not start until app destruction returned success:

```bash
terraform -chdir=terraform/app destroy -auto-approve -var=use_local_images=false -var=image_tag=v2.0.0
terraform -chdir=terraform/cluster destroy -auto-approve
```

Actual final output:

```text
kubernetes_namespace_v1.app: Destruction complete after 2m43s
Destroy complete! Resources: 7 destroyed.
kind_cluster.local: Destruction complete after 3s
Destroy complete! Resources: 2 destroyed.
Verified: dtq-tf-control-plane is absent.
```

Both `terraform state list` commands then returned no entries. The namespace finalized normally; no finalizer was stripped and no state repair was needed in this final release-test teardown. The dedicated test cluster is no longer running. The initial preparation error above is retained in this record instead of being represented as a clean destroy.
