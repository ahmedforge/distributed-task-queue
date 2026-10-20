#!/usr/bin/env bash
# Local demo only. Every kubectl call is pinned to this kind context/kubeconfig.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_BIN="${KIND_BIN:-kind}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
KUBECONFIG="${DTQ_KUBECONFIG:-$ROOT/.local-k8s/kubeconfig}"
CONTEXT=kind-dtq-v2
k() { "$KUBECTL_BIN" --kubeconfig "$KUBECONFIG" --context "$CONTEXT" "$@"; }
case "${1:-help}" in
  create)
    mkdir -p "$(dirname "$KUBECONFIG")"
    "$KIND_BIN" create cluster --name dtq-v2 --config "$ROOT/k8s/kind.yaml" --kubeconfig "$KUBECONFIG" --wait 180s
    docker update --cpus 2 --memory 3g --memory-swap 4g dtq-v2-control-plane
    ;;
  deploy)
    k get nodes >/dev/null
    "$ROOT/scripts/kind-load-images.sh" dtq-v2
    k apply -f "$ROOT/k8s/namespace.yaml"
    for file in config postgres redis minio; do k -n dtq apply -f "$ROOT/k8s/$file.yaml"; done
    for dep in postgres redis minio; do k -n dtq rollout status "deployment/$dep" --timeout=240s; done
    k -n dtq delete job bootstrap --ignore-not-found
    k -n dtq apply -f "$ROOT/k8s/bootstrap.yaml"
    k -n dtq wait --for=condition=complete job/bootstrap --timeout=300s
    k apply -k "$ROOT/k8s"
    for dep in api frontend worker; do k -n dtq rollout status "deployment/$dep" --timeout=240s; done
    k apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.21.0/keda-2.21.0.yaml
    for dep in keda-operator keda-metrics-apiserver keda-admission; do k -n keda rollout status "deployment/$dep" --timeout=300s; done
    k -n dtq apply -f "$ROOT/k8s/scaledobject.yaml"
    k -n dtq wait --for=condition=Ready scaledobject/worker-queue --timeout=120s
    ;;
  status) k -n dtq get pods,scaledobjects,hpa,pvc ;;
  open) k -n dtq port-forward --address 127.0.0.1 service/frontend "${DTQ_PORT:-3002}:8080" ;;
  delete) "$KIND_BIN" delete cluster --name dtq-v2 --kubeconfig "$KUBECONFIG" ;;
  *) echo "Usage: $0 {create|deploy|status|open|delete}" ;;
esac
