variable "image_repository" {
  description = "GHCR prefix before -service. Local mode overrides all four application images."
  type        = string
  default     = "ghcr.io/ahmedforge/distributed-task-queue"
}
variable "image_tag" {
  description = "Published version tag; use_local_images=true does not pull GHCR images."
  type        = string
  default     = "latest"
}
variable "use_local_images" {
  type    = bool
  default = true
}
variable "postgres_password" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.postgres_password) >= 12
    error_message = "Use a disposable local password of at least 12 characters."
  }
}
variable "minio_user" {
  type    = string
  default = "dtq-local"
}
variable "minio_password" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.minio_password) >= 12
    error_message = "Use a disposable local password of at least 12 characters."
  }
}
