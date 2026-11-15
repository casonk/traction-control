#!/usr/bin/env bash
# handover.sh pod-on|pod-off — start or suspend the CronJob. No sudo.
# The host timer and the pod must never both run: deploy.sh disables the timer before pod-on and suspends the
# pod before it enables the timer.
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/k3s.yaml}"
KUBECTL="${KUBECTL:-kubectl}"
case "${1:-}" in
  pod-on)  "$KUBECTL" -n @@NAMESPACE@@ patch cronjob @@WORKLOAD@@ -p '{"spec":{"suspend":false}}' ;;
  pod-off) "$KUBECTL" -n @@NAMESPACE@@ patch cronjob @@WORKLOAD@@ -p '{"spec":{"suspend":true}}' ;;
  *) echo "usage: $0 pod-on|pod-off"; exit 2 ;;
esac
