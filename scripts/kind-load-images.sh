#!/usr/bin/env bash
# Build and load the local application images into one explicitly named kind cluster.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_BIN="${KIND_BIN:-kind}"
CLUSTER="${1:-dtq-v2}"
docker build -t dtq-app:k8s "$ROOT"
docker build -t dtq-frontend:k8s "$ROOT/frontend"
if ! docker image inspect dtq-minio:k8s >/dev/null 2>&1; then
  docker build -f "$ROOT/docker/minio.Dockerfile" -t dtq-minio:k8s "$ROOT"
fi
for image in postgres:16-alpine redis:7-alpine; do
  docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
done
case "$(docker info --format '{{.Architecture}}')" in
  x86_64|amd64) platform=linux/amd64 ;;
  aarch64|arm64) platform=linux/arm64 ;;
  *) echo "Unsupported local node architecture" >&2; exit 1 ;;
esac
images=(dtq-app:k8s dtq-frontend:k8s dtq-minio:k8s postgres:16-alpine redis:7-alpine)
# Reuse optional upstream images already cached on this host; otherwise KEDA pulls.
for image in ghcr.io/kedacore/keda:2.21.0 ghcr.io/kedacore/keda-metrics-apiserver:2.21.0 ghcr.io/kedacore/keda-admission-webhooks:2.21.0; do
  if docker image inspect "$image" >/dev/null 2>&1; then images+=("$image"); fi
done
archive="$(mktemp /tmp/dtq-kind-images.XXXXXX.tar)"
trap 'rm -f "$archive"' EXIT
docker image save --platform "$platform" --output "$archive" "${images[@]}"
"$KIND_BIN" load image-archive --name "$CLUSTER" "$archive"
rm -f "$archive"
trap - EXIT
