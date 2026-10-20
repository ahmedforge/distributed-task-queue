# Terraform: local kind only

Terraform provisions a local kind cluster. Not deployed to a cloud provider.

## Prerequisites

Docker running on Linux/amd64, Terraform (verified with 1.16.6), kind 0.33.0,
and kubectl 1.35.x on PATH. The providers are pinned and both generated
`.terraform.lock.hcl` files are included for version control. Initial setup downloads providers, the node image, and KEDA.
Ports 18001 (API) and 3002 (frontend) must be free; override `api_port` and
`frontend_port` in an ignored `cluster/terraform.tfvars` if needed.

There are **two independent Terraform states**, applied in order. `cluster/`
creates kind and writes a dedicated kubeconfig. `app/` reads that state's outputs
and configures Kubernetes/Helm against that cluster, rather than your current
kubectl context. Splitting the stages avoids planning against a nonexistent API
or querying a ScaledObject CRD before KEDA installs it. No `-target` is needed.

## Exact local commands

Run from the repository root:

```bash
umask 077
terraform -chdir=terraform/cluster init
terraform -chdir=terraform/cluster fmt -check
terraform -chdir=terraform/cluster validate
terraform -chdir=terraform/cluster plan -out=cluster.tfplan
terraform -chdir=terraform/cluster apply cluster.tfplan

# Build local images and import the node platform using the shared kind helper.
./scripts/kind-load-images.sh "$(terraform -chdir=terraform/cluster output -raw cluster_name)"

cp terraform/app/terraform.tfvars.example terraform/app/terraform.tfvars
# Edit terraform/app/terraform.tfvars: replace both passwords with disposable values.
chmod 600 terraform/app/terraform.tfvars
terraform -chdir=terraform/app init
terraform -chdir=terraform/app fmt -check
terraform -chdir=terraform/app validate
terraform -chdir=terraform/app plan -out=app.tfplan
terraform -chdir=terraform/app apply app.tfplan

curl --fail-with-body "$(terraform -chdir=terraform/app output -raw api_url)/health"
# Frontend: http://127.0.0.1:3002 (or the frontend_url output).
kubectl --kubeconfig terraform/cluster/kubeconfig --context kind-dtq-tf -n dtq get pods,scaledobjects,hpa
```

Optional real-job autoscaling verification (uses the project's Python environment):

```bash
poetry run python scripts/verify_keda.py --cluster dtq-tf \
  --kubeconfig terraform/cluster/kubeconfig \
  --output /tmp/dtq-terraform-keda.json
```

If you override the cluster name, use its `kind-NAME` context and pass that name
to the verifier. The chart reads the existing `k8s/` YAML, changes image references
and API/frontend Service NodePorts, and runs the existing bootstrap Job as a
pre-install/pre-upgrade migration/bucket hook after infrastructure is ready.
KEDA installs through its pinned 2.21.0 Helm chart; the existing ScaledObject is
installed only after KEDA and the app. One worker remains for SAQ housekeeping.
Monitoring remains Compose-only because it is not present in the Kubernetes
manifests; this configuration does not add a second monitoring stack.

## Use published images instead

Once a version tag has actually published the four images and the GHCR packages
are public, change the ignored `app/terraform.tfvars`:

```hcl
use_local_images = false
image_repository = "ghcr.io/ahmedforge/distributed-task-queue"
image_tag        = "v1.0.0" # Example only; choose a version that actually exists.
```

This selects `PREFIX-api:TAG`, `PREFIX-worker:TAG`, `PREFIX-frontend:TAG`, and
`PREFIX-minio:TAG`. The bootstrap Job uses the API image. You can skip the local
build/load step in this mode. The chart retains `IfNotPresent`; prefer immutable
version or SHA tags. This demo does not configure private-registry credentials.
See [publishing](../docs/image-publishing.md) for the unverified-until-tagged
workflow and scanner policy.

## Destroy and reproduce

Destroy **app first, cluster second**, while the cluster API is reachable:

```bash
terraform -chdir=terraform/app plan -destroy -out=destroy.tfplan
terraform -chdir=terraform/app apply destroy.tfplan
terraform -chdir=terraform/cluster plan -destroy -out=destroy.tfplan
terraform -chdir=terraform/cluster apply destroy.tfplan
```

Equivalent interactive commands are `terraform -chdir=terraform/app destroy`
then `terraform -chdir=terraform/cluster destroy`. To reproduce from scratch,
repeat the cluster plan/apply, image load, and app plan/apply commands above;
keep the ignored tfvars file for your disposable credentials. Do not delete the
Docker node manually while its Terraform resources still exist.

Destroy removes the local node and PVC data. Neither Terraform stage manages
your Compose services or creates AWS/cloud resources. Kind cluster configuration
changes require recreation; the kind provider does not modify existing clusters.

## Secrets and state

`*.tfvars`, state/backups, plans, downloaded providers, and the generated kubeconfig
are ignored. Keep both lock files in Git. Required passwords have no defaults;
`terraform.tfvars.example` contains placeholders only. Sensitive Terraform values
are hidden in normal output but **remain in plaintext local state and plans**;
the kubeconfig also grants cluster-admin access. Protect these files and never
upload them. Do not use real account passwords for this disposable demo.

The single Docker node is limited to 2 CPUs / 3 GiB RAM / 4 GiB RAM+swap by a
small `terraform_data` Docker command because the kind provider has no resource
limit option. Worker pods share this node, so this is not multi-host availability.

## File responsibilities (interview guide)

| File | Purpose |
| --- | --- |
| `cluster/versions.tf` | Pins Terraform compatibility and the required kind provider. |
| `cluster/main.tf` | Creates/destroys one local node, limits its resources, binds localhost ports, and exports connection details. |
| `cluster/.terraform.lock.hcl` | Records the initialized kind provider version and checksums. |
| `app/versions.tf` | Pins the Helm and Kubernetes providers. |
| `app/variables.tf` | Selects local/GHCR images and accepts sensitive disposable passwords. |
| `app/main.tf` | Reads cluster outputs, reuses YAML, installs infrastructure/app/KEDA in order, and exports URLs. |
| `app/terraform.tfvars.example` | Shows configuration names with placeholder credentials; the real copy is ignored. |
| `app/.terraform.lock.hcl` | Records initialized Helm/Kubernetes provider versions and checksums. |
| `chart/Chart.yaml` | Identifies the minimal manifest-wrapper Helm chart. |
| `chart/values.yaml` | Declares the empty manifest list supplied by Terraform. |
| `chart/templates/manifests.yaml` | Emits supplied resources without copying application definitions. |

The shared `scripts/kind-load-images.sh` builds/imports local images for either
kind workflow; already-cached KEDA 2.21.0 images are imported too, otherwise
Kubernetes downloads them normally. `scripts/verify_keda.py --cluster NAME` runs the existing real-job
check against that named cluster, leaving its original default unchanged.

[Recorded commands, failures/recovery, and measured results](../docs/infrastructure-verification.md) distinguish local verification from the pending GHCR release run.

For application-code edits using the fixed local `:k8s` tags, import the rebuilt images and restart the affected Deployments. Terraform image variables select references; this setup does not provide an automatic code rollout pipeline.
