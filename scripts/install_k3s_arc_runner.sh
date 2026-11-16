#!/usr/bin/env bash
# install_k3s_arc_runner.sh — self-hosted GitHub Actions runners (ARC) on a K3s node that runs Cilium.
# No sudo. Safe to repeat. Pinned to ARC 0.15.1; change the pin as a deliberate step.
#
#   install_k3s_arc_runner.sh controller
#   install_k3s_arc_runner.sh scale-set OWNER/REPOSITORY NAME TOKEN_FILE
#   install_k3s_arc_runner.sh status
#   install_k3s_arc_runner.sh remove NAME
#
# A scale set serves ONE private repository (a personal account has no shared runners). NAME is the `runs-on` label.
# TOKEN_FILE is an owner-only file holding a fine-grained PAT scoped to that repository with Administration: read and
# write. It reaches the cluster only as a Secret, never on a command line. Public repositories are refused.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$HERE/../config/github-runners/k3s"
CHART_VERSION="0.15.1"
CHARTS="oci://ghcr.io/actions/actions-runner-controller-charts"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/k3s.yaml}"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }
need kubectl; need helm

controller() {
  kubectl wait --for=condition=Ready node --all --timeout=60s
  kubectl get crd ciliumnetworkpolicies.cilium.io >/dev/null \
    || fail "Cilium is not installed here; use config/github-runners/runner-network-policy.yaml instead"
  kubectl apply -f "$CONF/00-namespaces.yaml"
  kubectl apply -f "$CONF/10-cilium-policy.yaml"
  helm upgrade --install arc --namespace arc-systems --version "$CHART_VERSION" \
    --values "$CONF/controller-values.yaml" "$CHARTS/gha-runner-scale-set-controller"
  kubectl -n arc-systems rollout status deploy/arc-gha-rs-controller --timeout=180s
}

scale_set() {
  local slug="${1:-}" name="${2:-}" token_file="${3:-}"
  [[ "$slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "first argument must be OWNER/REPOSITORY"
  [[ "$name" =~ ^[a-z0-9][a-z0-9-]{1,40}$ ]] || fail "NAME must be lowercase letters, digits and hyphens"
  [[ -f "$token_file" && ! -L "$token_file" ]] || fail "token file missing or a symlink: $token_file"
  [[ "$(stat -c '%u %a' "$token_file")" == "$(id -u) 600" ]] || fail "token file must be mode 600 and owned by you"
  [[ -s "$token_file" ]] || fail "token file is empty"
  if command -v gh >/dev/null 2>&1; then
    [[ "$(gh api "repos/$slug" -q .private)" == "true" ]] \
      || fail "$slug is public (or unreadable): self-hosted runners are for private repositories only"
  else
    fail "gh is required to confirm the repository is private"
  fi
  kubectl -n arc-systems get deploy arc-gha-rs-controller >/dev/null 2>&1 || fail "run '$0 controller' first"

  workdir="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/arc-runner.XXXXXX")"; chmod 700 "$workdir"
  trap 'rm -rf "${workdir:-}"' EXIT
  sed -e "s|https://github.com/OWNER/REPOSITORY|https://github.com/$slug|" \
      -e "s|^runnerScaleSetName:.*|runnerScaleSetName: $name|" \
      "$CONF/scale-set-values.yaml.template" > "$workdir/values.yaml"
  mkdir "$workdir/secret"; install -m 600 "$token_file" "$workdir/secret/github_token"

  kubectl -n arc-runners create secret generic "runner-auth-$name" --from-file="$workdir/secret" \
    --dry-run=client -o yaml | kubectl apply -f -
  sed -i "s|^githubConfigSecret:.*|githubConfigSecret: runner-auth-$name|" "$workdir/values.yaml"
  helm upgrade --install "$name" --namespace arc-runners --version "$CHART_VERSION" \
    --values "$workdir/values.yaml" "$CHARTS/gha-runner-scale-set"
  kubectl -n arc-systems get pods
  echo "Scale set '$name' registered for $slug. Use runs-on: $name (see dot-github README, 'Choosing a runner')."
}

status() {
  kubectl -n arc-systems get pods
  kubectl -n arc-runners get pods,ephemeralrunners 2>/dev/null || true
  helm list -A
}

remove() {
  local name="${1:-}"
  [[ -n "$name" ]] || fail "NAME required"
  helm uninstall "$name" --namespace arc-runners
  kubectl -n arc-runners delete secret "runner-auth-$name" --ignore-not-found
  echo "Revoke the repository's fine-grained PAT in GitHub as well."
}

case "${1:-}" in
  controller) controller ;;
  scale-set) shift; scale_set "$@" ;;
  status) status ;;
  remove) shift; remove "$@" ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
