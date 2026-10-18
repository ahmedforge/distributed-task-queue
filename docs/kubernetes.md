# Local Kubernetes + KEDA demo

This is a single-node kind demo, isolated from the Compose stack. It is not a
cloud deployment, multi-node availability setup, or production configuration.

## Tools and setup

Install Docker, [kind v0.33.0](https://kind.sigs.k8s.io/docs/user/quick-start/)
and [kubectl 1.35.x](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/).
The kind config pins Kubernetes 1.35.8 and its image digest. KEDA 2.21.0 is pinned
in the helper. First use downloads a large node image and operator images.

From the repository root:

```bash
./scripts/k8s-local.sh create
./scripts/k8s-local.sh deploy
./scripts/k8s-local.sh status
./scripts/k8s-local.sh open
```

Open http://localhost:3002 while the last command runs. It forwards only to
localhost; no ingress, cloud account or public exposure is required.
`KIND_BIN` and `KUBECTL_BIN` can select explicit tool paths.
`DTQ_KUBECONFIG` can select another dedicated kubeconfig. The default is
`.local-k8s/kubeconfig` (gitignored). All kubectl commands explicitly select
`kind-dtq-v2`, so they do not depend on your current context.

The helper caps its new Docker node at 2 CPUs, 3 GiB RAM, and 4 GiB RAM+swap.
Existing user containers are not stopped. Use a machine with enough free RAM;
resource pressure affects this demo's timings. The base manifest resource
requests are deliberately small for local demonstration.

## Startup and storage

The helper exports only the node platform to an image archive, avoiding the
[Docker/containerd multi-platform import issue](https://kind.sigs.k8s.io/docs/user/known-issues/#unable-to-kind-load-docker-images). It then loads local application, frontend and MinIO images plus PostgreSQL
and Redis. It starts the three infrastructure Deployments, waits for readiness,
runs the migration/bucket-bootstrap Job, then starts API/frontend/workers.
Readiness probes gate API and frontend traffic. Worker processes start without
persistent volumes; only PostgreSQL, Redis and MinIO have PVCs.

Kind's default local-path storage keeps those PVCs on this one Docker node.
Prometheus/Grafana remain available through Compose and are not installed by
this Kubernetes bundle.

Redis AOF is enabled with `appendfsync everysec`; this is not a zero-data-loss
claim. PVCs survive ordinary pod replacement, but deleting the cluster destroys
the local demo data. The fixed credentials in `k8s/config.yaml` are intentionally
public local-demo values. Do not reuse them for a public deployment.

The frontend ConfigMap replaces Docker's DNS resolver with Kubernetes Service
resolution. Its API and WebSocket paths retain the same-origin proxy behavior.
The supplied manifests can be reviewed using `kubectl kustomize k8s`; the
bootstrap Job and ScaledObject are applied separately by the helper, after their
prerequisites exist.

## Scaling behavior

The [KEDA Redis Lists scaler](https://keda.sh/docs/2.21/scalers/redis-lists/)
monitors **`saq:tasks:queued`**, the actual SAQ ready-list key, with a target of
2 queued jobs per worker and a maximum of 3 replicas. Kubernetes' HPA controls scaling between 1 and 3 using its own reconciliation
interval (normally 15 seconds); activation polling is not tuned for this
minimum-one configuration.
Scale-down uses a 120-second stabilization window. This is queue-driven scaling,
not CPU scaling or evidence of linear throughput gains.

The minimum stays at **one worker**. SAQ needs a live worker for scheduling delayed
retries and sweeping interrupted attempts, and those jobs are not represented
by LLEN on the ready list. Scale-to-zero is deliberately unsupported here.

Worker dequeues have a one-second timeout, so an idle Redis wait cannot block
graceful exit indefinitely. A normal worker SIGTERM stops intake and gives SAQ up to 1800 seconds to drain,
with a 1860-second Kubernetes termination grace. SAQ's housekeeping loops can
also take time to exit. Default shutdown behavior outside this deployment is
unchanged. Forceful node/pod loss still relies on timeout/retry recovery; this
manifest does not add exactly-once execution or Redis-data-loss recovery.

## Verify with real jobs

Start from a fresh, idle demo with one ready worker:

```bash
poetry run python scripts/verify_keda.py
```

The script uses a temporary localhost frontend port-forward, uploads synthetic
20-page PDFs, records real queue lengths and desired/ready/pod counts, checks
that at least two workers become ready, verifies each output ZIP and PNG page,
and waits for the deployment to return to one worker with no terminating pods.
It records `docs/benchmarks/keda.json` and exits nonzero if any condition fails.
It leaves the cluster and workload jobs available for inspection. It does not
create fake Redis entries to trigger scaling.

Use `--kubectl PATH --kubeconfig PATH` when you installed tools/config elsewhere.
It is a manual integration demo, not part of every CI run.

## Recorded verification

Recorded October 18, 2026, using kind 0.33.0, Kubernetes 1.35.8, and KEDA
2.21.0. [Raw observations](benchmarks/keda.json) contain the queue depth,
desired/ready worker counts, pod counts, scaler configuration, and timestamps.

| Check | Observed result |
| --- | --- |
| Input workload | 16 synthetic PDFs, 20 simple text pages each; 4 concurrent uploads |
| Worker concurrency | 1 job per worker |
| Scale-up | 1 to 3 ready workers; first observed at 15.76 seconds |
| Completion and output checks | 16/16 completed; 16 intact ZIPs, 320 PNG signatures checked |
| Completion plus validation | 67.48 seconds from workload start |
| Frontend WebSocket proxy | Completed-job snapshot verified |
| Scale-down | 3 to 2 to 1; one remaining pod observed at 201.11 seconds |
| Runtime check | Exactly one running worker container before and after the experiment |

The node was capped at 2 CPUs and 3 GiB RAM (4 GiB RAM+swap) on a shared
7.53-GiB laptop with other services running. All three worker pods ran on that
same node and used MinIO, without worker PVCs. These results establish the local
queue-driven scaling and output checks for this workload; they do not measure
sustained capacity, throughput gains, or multi-host availability. Completion time
includes output validation and is not directly comparable to the v1 benchmark.
The test cluster was removed after saving evidence; rerun the setup to inspect
or demonstrate it.

The backend suite also passed all 80 tests, including real MinIO integration and
a regression check that idle Redis dequeues do not consume the entire job-drain
shutdown grace.

## Cleanup

```bash
./scripts/k8s-local.sh delete
```

This removes only the `dtq-v2` cluster and its local PVC data. It does not remove
the Compose demo or its volumes. Save evidence before cleanup.
