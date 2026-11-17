#!/usr/bin/env bash
# install_azure_runner_scale_sets.sh — one ARC runner scale set per private repository on the Azure runner VM.
#
#   install_azure_runner_scale_sets.sh [--repo SLUG ...] [--dry-run] [--print-script] [--skip-image] [--slugs-file FILE]
#
# Run it after azure_runner_stack.sh apply, install_azure_runner_k3s.sh, install_azure_runner_arc_controller.sh,
# configure_azure_runner_network_policy.sh and rotate_azure_runner_secret.sh (which stores the runner-auth Secret every
# scale set here uses). For each private repository in the visibility registry it renders
# config/github-runners/k3s/scale-set-values.yaml.template for the VM (2 vCPU, 4 GiB: one runner pod per repository,
# 1536Mi memory limit, at most 2 pods at once) and installs a scale set named <repo>-azure. It also builds the runner
# image (the upstream runner plus the CI tools) on the VM and imports it into k3s, because the VM has no registry.
# Afterwards it records the scale sets with runner_pool.py so `runner_pool.py set azure` accepts those repositories.
#
# --dry-run renders everything and prints the plan without touching Azure; --print-script also prints the script that
# would run on the VM (to stdout; the plan goes to stderr) so it can be reviewed or syntax-checked.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$HERE/../config/github-runners/k3s"
RESOURCE_GROUP="rg-arc-pilot"
VM_NAME="arcpilot-vm"
AZURE_CLI_IMAGE="mcr.microsoft.com/azure-cli:2.91.0"
ARC_VERSION="0.15.1"
RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-${HOME}/.azure}"
MEMORY_LIMIT="1536Mi"
DRY_RUN=0
PRINT_SCRIPT=0
SKIP_IMAGE=0
SLUGS_FILE=
WANTED=()

usage() { sed -n 2,18p "$0"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) [[ $# -ge 2 ]] || fail "--repo needs a value"; WANTED+=("$2"); shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --print-script) DRY_RUN=1; PRINT_SCRIPT=1; shift ;;
    --skip-image) SKIP_IMAGE=1; shift ;;
    --slugs-file) [[ $# -ge 2 ]] || fail "--slugs-file needs a value"; SLUGS_FILE="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

for c in python3 base64 sed; do command -v "$c" >/dev/null 2>&1 || fail "required command not found: $c"; done
[[ -f "$CONF/scale-set-values.yaml.template" ]] || fail "missing $CONF/scale-set-values.yaml.template"
[[ -f "$CONF/runner-image/Containerfile" ]] || fail "missing $CONF/runner-image/Containerfile"

POOL=(python3 "$HERE/runner_pool.py")
repo_args=()
for item in "${WANTED[@]+"${WANTED[@]}"}"; do repo_args+=(--repo "$item"); done
if [[ -n "$SLUGS_FILE" ]]; then
  mapfile -t SLUGS < "$SLUGS_FILE"
else
  mapfile -t SLUGS < <("${POOL[@]}" private)
fi
if [[ ${#WANTED[@]} -gt 0 ]]; then
  FILTERED=()
  for w in "${WANTED[@]}"; do
    found=""
    for s in "${SLUGS[@]}"; do
      if [[ "${s,,}" == "${w,,}" || "${s##*/}" == "${w##*/}" ]]; then found="$s"; fi
    done
    [[ -n "$found" ]] || fail "not a private repository in the registry: $w"
    FILTERED+=("$found")
  done
  SLUGS=("${FILTERED[@]}")
fi
[[ ${#SLUGS[@]} -gt 0 ]] || fail "no private repositories found"

WORK="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/azure-scale-sets.XXXXXX")"
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT

NAMES=()
index=0
PAYLOAD="set -eu
RUNNER_VERSION='${RUNNER_VERSION}'
ARC_VERSION='${ARC_VERSION}'
SKIP_IMAGE='${SKIP_IMAGE}'
CONTAINERFILE_B64='$(base64 -w0 "$CONF/runner-image/Containerfile")'
COUNT=${#SLUGS[@]}
"
for slug in "${SLUGS[@]}"; do
  base="${slug##*/}"
  name="$(printf '%s' "${base,,}" | tr '_.' '--')-azure"
  [[ "$name" =~ ^[a-z0-9][a-z0-9-]{1,40}$ ]] || fail "cannot form a scale-set name for $slug"
  values="$WORK/$name.yaml"
  sed -e "s|https://github.com/OWNER/REPOSITORY|https://github.com/${slug}|" \
      -e "s|^runnerScaleSetName:.*|runnerScaleSetName: ${name}|" \
      -e "s|image: localhost:5000/arc-runner:[0-9.]*|image: arc-runner:${RUNNER_VERSION}|" \
      -e "s|memory: 3Gi|memory: ${MEMORY_LIMIT}|" \
      "$CONF/scale-set-values.yaml.template" > "$values"
  grep -q "image: arc-runner:${RUNNER_VERSION}" "$values" || fail "the template's image line changed; update this script"
  grep -q "memory: ${MEMORY_LIMIT}" "$values" || fail "the template's memory limit changed; update this script"
  NAMES+=("$name")
  PAYLOAD+="NAME_${index}='${name}'
VALUES_${index}_B64='$(base64 -w0 "$values")'
"
  index=$((index + 1))
done

read -r -d '' REMOTE_BODY <<'REMOTE' || true
status=0
report() {
  result=$?
  [ -z "${tmp:-}" ] || rm -rf "$tmp"
  [ "$result" -eq 0 ] || status=$result
  printf '\n__SCALE_SETS_EXIT_STATUS=%s\n' "$status"
  exit 0
}
trap report EXIT
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl=/usr/local/bin/kubectl
command -v helm >/dev/null 2>&1 || { echo 'error: helm is missing; run install_azure_runner_arc_controller.sh first' >&2; exit 1; }
$kubectl -n arc-systems get deploy arc-gha-rs-controller >/dev/null 2>&1 \
  || { echo 'error: the ARC controller is not installed' >&2; exit 1; }
$kubectl get ns arc-runners >/dev/null 2>&1 || $kubectl create ns arc-runners
$kubectl -n arc-runners get secret runner-auth >/dev/null 2>&1 \
  || { echo 'error: the runner-auth Secret is missing; run rotate_azure_runner_secret.sh first' >&2; exit 1; }
$kubectl label ns arc-runners pod-security.kubernetes.io/enforce=restricted --overwrite >/dev/null
cat <<'QUOTA' | $kubectl apply -f -
apiVersion: v1
kind: ResourceQuota
metadata:
  name: arc-runner-pods
  namespace: arc-runners
spec:
  hard:
    pods: "2"
QUOTA

tmp="$(mktemp -d)"

if [ "$SKIP_IMAGE" != "1" ]; then
  if ! k3s ctr -n k8s.io images ls -q | grep -q "arc-runner:${RUNNER_VERSION}\$"; then
    if ! command -v buildah >/dev/null 2>&1; then
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y -qq buildah >/dev/null
    fi
    mkdir "$tmp/image"
    printf '%s' "$CONTAINERFILE_B64" | base64 -d > "$tmp/image/Containerfile"
    buildah bud --isolation chroot --build-arg "RUNNER_VERSION=${RUNNER_VERSION}" \
      -t "localhost/arc-runner:${RUNNER_VERSION}" -f "$tmp/image/Containerfile" "$tmp/image" >/dev/null
    buildah push "localhost/arc-runner:${RUNNER_VERSION}" \
      "docker-archive:$tmp/arc-runner.tar:arc-runner:${RUNNER_VERSION}" >/dev/null
    k3s ctr -n k8s.io images import "$tmp/arc-runner.tar" >/dev/null
    buildah rmi -a >/dev/null 2>&1 || true
  fi
  k3s ctr -n k8s.io images ls -q | grep -q "arc-runner:${RUNNER_VERSION}\$" \
    || { echo 'error: the runner image is not in k3s after the build' >&2; exit 1; }
  echo "runner image arc-runner:${RUNNER_VERSION} present"
fi

i=0
while [ "$i" -lt "$COUNT" ]; do
  eval "name=\$NAME_${i}"
  eval "values_b64=\$VALUES_${i}_B64"
  printf '%s' "$values_b64" | base64 -d > "$tmp/$name.yaml"
  if helm upgrade --install "$name" --namespace arc-runners --version "$ARC_VERSION" \
      --values "$tmp/$name.yaml" oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set >/dev/null; then
    echo "__SCALE_SET_OK $name"
  else
    echo "__SCALE_SET_FAILED $name"
  fi
  i=$((i + 1))
done
helm list -n arc-runners --short
$kubectl -n arc-systems get pods --no-headers | awk '{print $1, $3}'
REMOTE
REMOTE_SCRIPT="${PAYLOAD}${REMOTE_BODY}"

say() { if (( PRINT_SCRIPT == 1 )); then printf "$@" >&2; else printf "$@"; fi; }
say 'Scale sets to install on %s/%s (%s):\n' "$RESOURCE_GROUP" "$VM_NAME" "${#NAMES[@]}"
for n in "${!SLUGS[@]}"; do say '  %-26s -> %s\n' "${NAMES[$n]}" "${SLUGS[$n]}"; done
say 'Runner image: arc-runner:%s (built on the VM%s), memory limit %s, quota 2 pods.\n' \
  "$RUNNER_VERSION" "$([[ $SKIP_IMAGE == 1 ]] && printf ', skipped')" "$MEMORY_LIMIT"

if (( DRY_RUN == 1 )); then
  say 'dry run: remote script is %s bytes; nothing was sent to Azure.\n' "${#REMOTE_SCRIPT}"
  if (( PRINT_SCRIPT == 1 )); then printf '%s\n' "$REMOTE_SCRIPT"; fi
  [[ ${#REMOTE_SCRIPT} -lt 120000 ]] || fail "the remote script is too large for one Azure Run Command call; use --repo to split it"
  exit 0
fi
[[ ${#REMOTE_SCRIPT} -lt 120000 ]] || fail "the remote script is too large for one Azure Run Command call; use --repo to split it"

command -v podman >/dev/null 2>&1 || fail "required command not found: podman"
command -v stat >/dev/null 2>&1 && command -v find >/dev/null 2>&1 && command -v grep >/dev/null 2>&1 \
  || fail "stat, find and grep are required"
[[ -d "$AZURE_CONFIG_DIR" && ! -L "$AZURE_CONFIG_DIR" ]] \
  || fail "Azure CLI config directory is missing or is a symlink: ${AZURE_CONFIG_DIR}"
[[ "$(stat -c '%u' "$AZURE_CONFIG_DIR")" == "$(id -u)" ]] || fail "Azure CLI config directory must be owned by you"
[[ "$(stat -c '%a' "$AZURE_CONFIG_DIR")" == "700" ]] || fail "Azure CLI config directory must have mode 0700"
if find "$AZURE_CONFIG_DIR" -type l -print -quit | grep -q .; then fail "Azure CLI config must not contain symlinks"; fi
unsafe="$(find "$AZURE_CONFIG_DIR" \( -type f -o -type d \) -perm /077 -print -quit)" || fail "could not validate permissions"
[[ -z "$unsafe" ]] || fail "Azure CLI files must not grant group or other access (chmod -R go-rwx ~/.azure)"
podman image exists "$AZURE_CLI_IMAGE" || fail "Azure CLI image is not available locally: ${AZURE_CLI_IMAGE}"

printf 'This installs %s scale sets and builds the runner image on the VM, which stays billable.\n' "${#NAMES[@]}"
printf 'Type SCALESETS to proceed: '
read -r confirmation
[[ "$confirmation" == "SCALESETS" ]] || fail "operation cancelled"

export SCALE_SETS_SETUP="$REMOTE_SCRIPT"
output="$(podman run --rm \
  --env SCALE_SETS_SETUP \
  --volume "${AZURE_CONFIG_DIR}:/source:ro,z" \
  --entrypoint /bin/sh \
  "$AZURE_CLI_IMAGE" \
  -c '
    set -eu
    state_dir="$(mktemp -d /tmp/azure-state.XXXXXX)"
    trap '\''rm -rf "$state_dir"'\'' EXIT
    cp -a /source/. "$state_dir/"
    export AZURE_CONFIG_DIR="$state_dir"
    [ "$(az account show --only-show-errors --query state --output tsv)" = "Enabled" ] || {
      printf "error: Azure subscription is not enabled\n" >&2; exit 1; }
    vm_state="$(az vm get-instance-view --resource-group rg-arc-pilot --name arcpilot-vm --only-show-errors \
      --query "instanceView.statuses[?starts_with(code, '\''PowerState/'\'')].displayStatus | [0]" --output tsv)"
    [ "$vm_state" = "VM running" ] || { printf "error: Azure VM is not running (state: %s)\n" "$vm_state" >&2; exit 1; }
    az vm run-command invoke --resource-group rg-arc-pilot --name arcpilot-vm --command-id RunShellScript \
      --scripts "$SCALE_SETS_SETUP" --only-show-errors --query "value[].message" --output tsv
  ')" || fail "the Azure call failed; the output above is all there is"

printf '%s\n' "$output" | grep -v -E '^__SCALE_SET' || true
printf '%s\n' "$output" | grep -Fxq "__SCALE_SETS_EXIT_STATUS=0" \
  || fail "the setup failed inside the VM; inspect the output above"

ok_repos=()
for n in "${!SLUGS[@]}"; do
  if printf '%s\n' "$output" | grep -Fxq "__SCALE_SET_OK ${NAMES[$n]}"; then
    ok_repos+=(--repo "${SLUGS[$n]}")
  else
    printf 'warning: scale set %s did not report success\n' "${NAMES[$n]}" >&2
  fi
done
if [[ ${#ok_repos[@]} -gt 0 ]]; then
  "${POOL[@]}" enroll azure "${ok_repos[@]}"
  printf 'Next: python3 scripts/runner_pool.py set azure   (and python3 scripts/runner_pool.py status)\n'
else
  fail "no scale set reported success"
fi
