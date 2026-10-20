data "terraform_remote_state" "cluster" {
  backend = "local"
  config = {
    path = "${path.module}/../cluster/terraform.tfstate"
  }
}

provider "kubernetes" {
  config_path    = data.terraform_remote_state.cluster.outputs.kubeconfig_path
  config_context = "kind-${data.terraform_remote_state.cluster.outputs.cluster_name}"
}
provider "helm" {
  kubernetes = {
    config_path    = data.terraform_remote_state.cluster.outputs.kubeconfig_path
    config_context = "kind-${data.terraform_remote_state.cluster.outputs.cluster_name}"
  }
}

locals {
  manifest_dir = "${path.module}/../../k8s"
  images = var.use_local_images ? {
    api      = "dtq-app:k8s"
    worker   = "dtq-app:k8s"
    frontend = "dtq-frontend:k8s"
    minio    = "dtq-minio:k8s"
    } : {
    for service in ["api", "worker", "frontend", "minio"] :
    service => "${var.image_repository}-${service}:${var.image_tag}"
  }
  # Read the existing multi-document YAML; do not maintain a second deployment.
  infra = flatten([
    for name in ["postgres", "redis", "minio"] : [
      for doc in split("\n---\n", replace(file("${local.manifest_dir}/${name}.yaml"), "dtq-minio:k8s", local.images.minio)) : yamldecode(doc)
    ]
  ])
  app = flatten([
    for name in ["api", "worker", "frontend"] : [
      for doc in split("\n---\n", replace(replace(file("${local.manifest_dir}/${name}.yaml"), "dtq-app:k8s", local.images[name]), "dtq-frontend:k8s", local.images.frontend)) : yamldecode(doc)
    ]
  ])
  config    = yamldecode(split("\n---\n", file("${local.manifest_dir}/config.yaml"))[0])
  bootstrap = yamldecode(replace(file("${local.manifest_dir}/bootstrap.yaml"), "dtq-app:k8s", local.images.api))
  # Run migrations and bucket creation before application pods start.
  bootstrap_hook = merge(local.bootstrap, {
    metadata = merge(local.bootstrap.metadata, {
      annotations = {
        "helm.sh/hook"               = "pre-install,pre-upgrade"
        "helm.sh/hook-delete-policy" = "before-hook-creation,hook-succeeded"
      }
    })
  })
}

resource "kubernetes_namespace_v1" "app" {
  metadata { name = "dtq" }
}
resource "kubernetes_secret_v1" "demo" {
  metadata {
    name      = "demo-secrets"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
  }
  data = {
    POSTGRES_PASSWORD   = var.postgres_password
    S3_ACCESS_KEY       = var.minio_user
    S3_SECRET_KEY       = var.minio_password
    MINIO_ROOT_USER     = var.minio_user
    MINIO_ROOT_PASSWORD = var.minio_password
  }
}
resource "kubernetes_config_map_v1" "frontend" {
  metadata {
    name      = "frontend-proxy"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
  }
  data = { "frontend.conf" = file("${local.manifest_dir}/frontend.conf") }
}

resource "helm_release" "infra" {
  name       = "dtq-infra"
  namespace  = kubernetes_namespace_v1.app.metadata[0].name
  chart      = "${path.module}/../chart"
  timeout    = 600
  wait       = true
  values     = [yamlencode({ manifests = concat([local.config], local.infra) })]
  depends_on = [kubernetes_secret_v1.demo]
}

resource "helm_release" "keda" {
  name             = "keda"
  namespace        = "keda"
  create_namespace = true
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = "2.21.0"
  timeout          = 1200
  atomic           = true
  wait             = true
}

resource "helm_release" "app" {
  name          = "dtq-app"
  namespace     = kubernetes_namespace_v1.app.metadata[0].name
  chart         = "${path.module}/../chart"
  timeout       = 600
  wait          = true
  wait_for_jobs = true
  values = [yamlencode({ manifests = concat([local.bootstrap_hook], [
    for doc in local.app : yamldecode(doc.kind == "Service" ? yamlencode(merge(doc, {
      spec = merge(doc.spec, {
        type = "NodePort"
        ports = [merge(doc.spec.ports[0], {
          nodePort = doc.metadata.name == "api" ? 30080 : 30081
        })]
      })
    })) : yamlencode(doc))
  ]) })]
  depends_on = [helm_release.infra, kubernetes_config_map_v1.frontend]
}

# A Helm release avoids Kubernetes-provider CRD discovery during a fresh plan.
resource "helm_release" "scaler" {
  name       = "dtq-scaler"
  namespace  = kubernetes_namespace_v1.app.metadata[0].name
  chart      = "${path.module}/../chart"
  timeout    = 300
  values     = [yamlencode({ manifests = [yamldecode(file("${local.manifest_dir}/scaledobject.yaml"))] })]
  depends_on = [helm_release.app, helm_release.keda]
}

output "cluster_name" {
  value = data.terraform_remote_state.cluster.outputs.cluster_name
}
output "api_url" {
  value = data.terraform_remote_state.cluster.outputs.api_url
}
output "frontend_url" {
  value = data.terraform_remote_state.cluster.outputs.frontend_url
}
