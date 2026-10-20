variable "cluster_name" {
  type    = string
  default = "dtq-tf"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}$", var.cluster_name))
    error_message = "Use a short lowercase kind cluster name."
  }
}

variable "api_port" {
  type    = number
  default = 18001
}

variable "frontend_port" {
  type    = number
  default = 3002
}

resource "kind_cluster" "local" {
  name            = var.cluster_name
  node_image      = yamldecode(file("${path.module}/../../k8s/kind.yaml")).nodes[0].image
  wait_for_ready  = true
  kubeconfig_path = abspath("${path.root}/kubeconfig")
  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"
    node {
      role = "control-plane"
      extra_port_mappings {
        container_port = 30080
        host_port      = var.api_port
        listen_address = "127.0.0.1"
      }
      extra_port_mappings {
        container_port = 30081
        host_port      = var.frontend_port
        listen_address = "127.0.0.1"
      }
    }
  }
}

# Local Docker resource limits are not exposed by the kind provider.
resource "terraform_data" "node_limits" {
  triggers_replace = [kind_cluster.local.id]
  provisioner "local-exec" {
    command = "docker update --cpus 2 --memory 3g --memory-swap 4g ${var.cluster_name}-control-plane"
  }
}

output "cluster_name" {
  value = kind_cluster.local.name
}
output "kubeconfig_path" {
  value = kind_cluster.local.kubeconfig_path
}
output "api_url" {
  value = "http://127.0.0.1:${var.api_port}"
}
output "frontend_url" {
  value = "http://127.0.0.1:${var.frontend_port}"
}
