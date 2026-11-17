#!/usr/bin/env bash
# install_azure_win_broker.sh — build and deploy the Windows runner broker on the Azure Linux VM.
#
#   install_azure_win_broker.sh [--dry-run-broker | --live] [--with-linux-vm] [--print-plan]
#
# Prerequisites: the Azure Linux runner pool (azure_runner_stack.sh and the install helpers), the Windows stack
# (azure_windows_runner_stack.sh) with a golden image (bake_azure_windows_image.sh), and a private-repository
# allowlist in config/github-runners/windows/allowlist.local.txt (copy allowlist.example.txt; ignored by Git).
#
# It (1) gives the Linux VM a system-assigned managed identity with Contributor on the Windows resource group only,
# (2) builds the broker image on the VM from this checkout (the VM has no registry), (3) copies the GitHub token from
# the runner-auth Secret to the broker's own Secret inside the cluster (it never leaves the VM), and (4) applies
# config/github-runners/windows/win-broker.yaml.template. --dry-run-broker (the default) deploys the broker with
# WIN_BROKER_DRY_RUN=1: it registers its scale sets and logs what it would create, but creates no VM. --live
# deploys it for real. --with-linux-vm also enables the premium per-job Linux VM class (see the runbook); it is OFF
# unless that flag is given and a Linux golden image is recorded. --print-plan only prints what would happen.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
CONF="$ROOT/config/github-runners/windows"
SERVICE="$ROOT/services/win-broker"
LINUX_RG="rg-arc-pilot"
LINUX_VM="arcpilot-vm"
WIN_RG="rg-win-runner"
VNET="winrun-vnet"
SUBNET="winrun-subnet"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-windows-runner"
MODE="dry"
PLAN_ONLY=0
WITH_LINUX=0

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run-broker) MODE="dry"; shift ;;
    --live) MODE="live"; shift ;;
    --with-linux-vm) WITH_LINUX=1; shift ;;
    --print-plan) PLAN_ONLY=1; shift ;;
    --help|-h) sed -n 2,18p "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

for c in az gh tar base64 gzip date; do command -v "$c" >/dev/null 2>&1 || fail "required command not found: $c"; done
[[ -f "$CONF/win-broker.yaml.template" && -f "$SERVICE/Containerfile" ]] || fail "missing broker files"
[[ -f "$CONF/allowlist.local.txt" ]] || fail "create $CONF/allowlist.local.txt first (copy allowlist.example.txt)"
[[ -f "$STATE_DIR/image-version.txt" ]] || fail "no golden image recorded; run scripts/bake_azure_windows_image.sh first"

REPOS=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [[ -z "$line" ]] && continue
  [[ "$line" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "not owner/name: $line"
  [[ "$line" != "OWNER/REPOSITORY" ]] || fail "the allowlist still has the example entry"
  REPOS+=("$line")
done < "$CONF/allowlist.local.txt"
[[ ${#REPOS[@]} -gt 0 ]] || fail "the allowlist is empty"
for r in "${REPOS[@]}"; do
  [[ "$(gh api "repos/$r" -q .private)" == "true" ]] || fail "$r is public or unreadable: self-hosted runners are for private repositories only"
done

IMAGE_ID="$(tr -d '[:space:]' < "$STATE_DIR/image-version.txt")"
LINUX_IMAGE_ID=""
if (( WITH_LINUX == 1 )); then
  [[ -f "$STATE_DIR/linux-image-version.txt" ]] || fail "--with-linux-vm needs a Linux golden image id in $STATE_DIR/linux-image-version.txt (see the runbook's premium section)"
  LINUX_IMAGE_ID="$(tr -d '[:space:]' < "$STATE_DIR/linux-image-version.txt")"
  [[ "$LINUX_IMAGE_ID" == /subscriptions/* ]] || fail "linux-image-version.txt does not hold an Azure image version id"
fi
SUBSCRIPTION_ID="$(az account show --query id -o tsv --only-show-errors)"
LOCATION="$(az group show -n "$WIN_RG" --query location -o tsv --only-show-errors)"
SUBNET_ID="$(az network vnet subnet show -g "$WIN_RG" --vnet-name "$VNET" -n "$SUBNET" --query id -o tsv --only-show-errors)"
VERSION="$(date -u +%Y%m%d%H%M)"
DRY=0; [[ "$MODE" == "dry" ]] && DRY=1
REPOS_JSON="$(printf '"%s",' "${REPOS[@]}")"; REPOS_JSON="[${REPOS_JSON%,}]"

printf 'Broker version : %s\nMode           : %s\nRepositories   : %s\nImage          : %s\nLinux VM class : %s\n' \
  "$VERSION" "$([[ $DRY == 1 ]] && echo 'dry run (creates no VM)' || echo LIVE)" "${REPOS[*]}" "${IMAGE_ID##*/}" \
  "$([[ $WITH_LINUX == 1 ]] && echo "ENABLED (premium): ${LINUX_IMAGE_ID##*/}" || echo 'off')"
if (( PLAN_ONLY == 1 )); then exit 0; fi

printf 'This changes the Linux VM (managed identity, role assignment, a new namespace and image). Type BROKER to continue: '
read -r answer
[[ "$answer" == "BROKER" ]] || fail "operation cancelled"

printf '== managed identity\n'
principal="$(az vm identity assign -g "$LINUX_RG" -n "$LINUX_VM" --query systemAssignedIdentity -o tsv --only-show-errors)"
[[ -n "$principal" ]] || fail "could not enable the system-assigned identity"
scope="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${WIN_RG}"
if [[ -z "$(az role assignment list --assignee "$principal" --scope "$scope" --role Contributor --query '[0].id' -o tsv --only-show-errors)" ]]; then
  az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
    --role Contributor --scope "$scope" --only-show-errors >/dev/null
  printf 'granted Contributor on %s only\n' "$WIN_RG"
else
  printf 'Contributor on %s already granted\n' "$WIN_RG"
fi

WORK="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/win-broker-install.XXXXXX")"
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT
(cd "$SERVICE" && tar -czf "$WORK/src.tgz" --exclude='*_test.go' --exclude='.git' .)
SRC_B64="$(base64 -w0 "$WORK/src.tgz")"
sed -e "s|__VERSION__|${VERSION}|g" -e "s|__DRY_RUN__|${DRY}|g" -e "s|__SUBSCRIPTION_ID__|${SUBSCRIPTION_ID}|g" \
    -e "s|__RESOURCE_GROUP__|${WIN_RG}|g" -e "s|__LOCATION__|${LOCATION}|g" -e "s|__SUBNET_ID__|${SUBNET_ID}|g" \
    -e "s|__IMAGE_ID__|${IMAGE_ID}|g" -e "s|__REPOS_JSON__|${REPOS_JSON}|g" \
    -e "s|__LINUX_VM__|${WITH_LINUX}|g" -e "s|__LINUX_IMAGE_ID__|${LINUX_IMAGE_ID}|g" \
    "$CONF/win-broker.yaml.template" > "$WORK/manifest.yaml"
! grep -q '__[A-Z_]*__' "$WORK/manifest.yaml" || fail "an unfilled placeholder is left in the manifest"
MANIFEST_B64="$(base64 -w0 "$WORK/manifest.yaml")"

read -r -d '' REMOTE_BODY <<'REMOTE' || true
status=0
report() { r=$?; [ -z "${tmp:-}" ] || rm -rf "$tmp"; [ "$r" -eq 0 ] || status=$r; printf '\n__BROKER_EXIT_STATUS=%s\n' "$status"; exit 0; }
trap report EXIT
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl=/usr/local/bin/kubectl
tmp="$(mktemp -d)"
$kubectl -n arc-runners get secret runner-auth >/dev/null 2>&1 || { echo 'error: the runner-auth Secret is missing' >&2; exit 1; }
command -v buildah >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq buildah >/dev/null; }

mkdir "$tmp/src"
printf '%s' "$SRC_B64" | base64 -d | tar -xz -C "$tmp/src"
buildah bud --isolation chroot --build-arg "VERSION=$VERSION" -t "localhost/win-broker:$VERSION" -f "$tmp/src/Containerfile" "$tmp/src" >/dev/null
buildah push "localhost/win-broker:$VERSION" "docker-archive:$tmp/wb.tar:win-broker:$VERSION" >/dev/null
k3s ctr -n k8s.io images import "$tmp/wb.tar" >/dev/null
buildah rmi -a >/dev/null 2>&1 || true
echo "image win-broker:$VERSION imported"

printf '%s' "$MANIFEST_B64" | base64 -d > "$tmp/manifest.yaml"
$kubectl apply -f "$tmp/manifest.yaml" -l '!app' >/dev/null 2>&1 || true
$kubectl get ns win-broker >/dev/null 2>&1 || $kubectl apply -f "$tmp/manifest.yaml" >/dev/null 2>&1 || true
token_b64="$($kubectl -n arc-runners get secret runner-auth -o jsonpath='{.data.github_token}')"
[ -n "$token_b64" ] || { echo 'error: runner-auth has no github_token key' >&2; exit 1; }
cat <<SECRET | $kubectl apply -f - >/dev/null
apiVersion: v1
kind: Secret
metadata:
  name: broker-token
  namespace: win-broker
type: Opaque
data:
  token: $token_b64
SECRET
unset token_b64
echo "token copied inside the cluster"
$kubectl apply -f "$tmp/manifest.yaml"
$kubectl -n win-broker rollout status deploy/win-broker --timeout=180s
sleep 20
$kubectl -n win-broker logs deploy/win-broker --tail=40 2>&1 | tail -40
REMOTE
PAYLOAD="set -eu
VERSION='${VERSION}'
SRC_B64='${SRC_B64}'
MANIFEST_B64='${MANIFEST_B64}'
"
REMOTE_SCRIPT="${PAYLOAD}${REMOTE_BODY}"
[[ ${#REMOTE_SCRIPT} -lt 125000 ]] || fail "the remote script is ${#REMOTE_SCRIPT} bytes, over the Run Command limit"

printf '== building and deploying on the VM (%s bytes sent)\n' "${#REMOTE_SCRIPT}"
output="$(az vm run-command invoke -g "$LINUX_RG" -n "$LINUX_VM" --command-id RunShellScript \
  --scripts "$REMOTE_SCRIPT" --only-show-errors --query 'value[].message' -o tsv)"
printf '%s\n' "$output"
printf '%s\n' "$output" | grep -Fxq "__BROKER_EXIT_STATUS=0" || fail "the deployment failed inside the VM; see the output above"
printf 'Deployed. Watch it: kubectl -n win-broker logs deploy/win-broker (on the VM)\n'
