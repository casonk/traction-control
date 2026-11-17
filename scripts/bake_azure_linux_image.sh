#!/usr/bin/env bash
# bake_azure_linux_image.sh — build the golden Linux runner image for the broker's premium per-job Linux VM class.
#
#   bake_azure_linux_image.sh [--dry-run] [--skip-verify | --verify-only]
#
# Creates a short-lived template VM from Ubuntu 24.04 in the Windows stack's subnet (no inbound port is open), installs
# the CI tools and the pinned Actions runner on it through Run Command (config/github-runners/linux-vm/bake-image.sh),
# deprovisions and generalizes it, captures a new version of the `linux-runner` image in the gallery, and deletes the
# template VM. Unless --skip-verify, it then creates a throwaway VM from the new image with the same cloud-init
# script the broker uses (a deliberately invalid runner configuration, so no runner can register), proves the runner
# started and the tools are present, and deletes that VM too. Nothing here enables the broker's Linux class.
# The new version id is recorded under the owner-only state directory (linux-image-version.txt). --verify-only skips
# the bake and runs the verification against the version already recorded there.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$HERE/../config/github-runners/linux-vm"
RESOURCE_GROUP="rg-win-runner"
GALLERY="winrungallery"
IMAGE="linux-runner"
VNET="winrun-vnet"
SUBNET="winrun-subnet"
BAKE_VM="lbake-vm"
VERIFY_VM="lverify-vm"
VM_SIZE="${LINUX_BAKE_VM_SIZE:-Standard_B2als_v2}"
BASE_IMAGE="Canonical:ubuntu-24_04-lts:server:latest"
RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
KEY_FILE="${AZURE_RUNNER_SSH_PUBLIC_KEY_FILE:-${HOME}/.ssh/id_ed25519.pub}"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-windows-runner"
DRY_RUN=0
VERIFY=1
VERIFY_ONLY=0

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --skip-verify) VERIFY=0 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --help|-h) sed -n 2,16p "$0"; exit 0 ;;
    *) fail "unknown argument: $arg" ;;
  esac
done

[[ "$RUNNER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "RUNNER_VERSION must look like 2.338.2"
for c in az base64 date; do command -v "$c" >/dev/null 2>&1 || fail "required command not found: $c"; done
[[ -f "$CONF/bake-image.sh" ]] || fail "missing $CONF/bake-image.sh"
[[ -f "$KEY_FILE" && ! -L "$KEY_FILE" ]] || fail "SSH public key file is missing or is a symlink: $KEY_FILE"
grep -q 'PRIVATE KEY' "$KEY_FILE" && fail "the key file holds a private key; pass the public key only"
read -r SSH_PUBLIC_KEY < "$KEY_FILE"

VERSION="$(date -u +%Y).$(date -u +%m%d).$(date -u +%H%M)"
printf 'Template VM : %s (%s, %s)\nGallery     : %s/%s\nNew version : %s\nRunner      : %s\nVerify      : %s\n' \
  "$BAKE_VM" "$BASE_IMAGE" "$VM_SIZE" "$GALLERY" "$IMAGE" "$VERSION" "$RUNNER_VERSION" "$([[ $VERIFY == 1 ]] && echo yes || echo skipped)"
if (( DRY_RUN == 1 )); then printf 'dry run: nothing was changed.\n'; exit 0; fi

printf 'This creates two short-lived VMs (about $0.04 an hour each) and one image version, and deletes the VMs. Type BAKE to continue: '
read -r answer
[[ "$answer" == "BAKE" ]] || fail "operation cancelled"

SUBNET_ID="$(az network vnet subnet show -g "$RESOURCE_GROUP" --vnet-name "$VNET" -n "$SUBNET" --query id -o tsv --only-show-errors)"
WORK="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/bake-linux.XXXXXX")"
chmod 700 "$WORK"

delete_vm() {
  local name="$1"
  az vm delete -g "$RESOURCE_GROUP" -n "$name" --yes --force-deletion true --only-show-errors >/dev/null 2>&1 || true
  az network nic delete -g "$RESOURCE_GROUP" -n "${name}VMNic" --only-show-errors >/dev/null 2>&1 || true
  az network public-ip delete -g "$RESOURCE_GROUP" -n "${name}-pip" --only-show-errors >/dev/null 2>&1 || true
  az disk delete -g "$RESOURCE_GROUP" -n "${name}-osdisk" --yes --only-show-errors >/dev/null 2>&1 || true
  az network nsg delete -g "$RESOURCE_GROUP" -n "${name}NSG" --only-show-errors >/dev/null 2>&1 || true
}
CAPTURED=0
cleanup() {
  rm -rf "$WORK"
  delete_vm "$BAKE_VM"
  delete_vm "$VERIFY_VM"
  if (( CAPTURED == 0 )); then  # a failed capture leaves an empty version that would only confuse the next run
    az sig image-version delete -g "$RESOURCE_GROUP" --gallery-name "$GALLERY" --gallery-image-definition "$IMAGE" \
      --gallery-image-version "$VERSION" --only-show-errors >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

create_vm() {  # name image [custom-data-file]
  local name="$1" image="$2" cd_file="${3:-}"
  local args=(vm create -g "$RESOURCE_GROUP" -n "$name" --image "$image" --size "$VM_SIZE" --subnet "$SUBNET_ID"
    --public-ip-address "${name}-pip" --public-ip-sku Standard --admin-username runneradmin
    --authentication-type ssh --ssh-key-values "$SSH_PUBLIC_KEY" --os-disk-name "${name}-osdisk"
    --os-disk-delete-option Delete --nic-delete-option Delete --nsg "" --security-type TrustedLaunch --enable-secure-boot true --enable-vtpm true --tags purpose=linux-image-bake --only-show-errors -o none)
  [[ -n "$cd_file" ]] && args+=(--custom-data "$cd_file")
  az "${args[@]}"
}

wait_for_agent() {  # name: a new VM's agent needs a minute or two before Run Command works
  local status=""
  for _ in $(seq 1 40); do
    status="$(az vm get-instance-view -g "$RESOURCE_GROUP" -n "$1" --only-show-errors \
      --query 'instanceView.vmAgent.statuses[0].displayStatus' -o tsv 2>/dev/null || true)"
    [[ "$status" == "Ready" ]] && return 0
    sleep 10
  done
  fail "the VM agent on $1 never became ready (last status: ${status:-none})"
}

run_script() {  # name script-file
  az vm run-command invoke -g "$RESOURCE_GROUP" -n "$1" --command-id RunShellScript --scripts @"$2" \
    --only-show-errors --query 'value[].message' -o tsv
}

bake_image() {
printf '== creating the template VM\n'
create_vm "$BAKE_VM" "$BASE_IMAGE"

printf '== waiting for the VM agent\n'
wait_for_agent "$BAKE_VM"

printf '== baking (tools, runner user, the Actions runner)\n'
{ printf "RUNNER_VERSION='%s'\n" "$RUNNER_VERSION"; cat "$CONF/bake-image.sh"; } > "$WORK/bake.sh"
output="$(run_script "$BAKE_VM" "$WORK/bake.sh")"
printf '%s\n' "$output" | grep -E '^(verified|git=|docker=|jq=|runner=|sudo=|baked|error)' || true
install -d -m 700 "$STATE_DIR/manifests"
(umask 077; printf '%s\n' "$output" | grep -E '^(built|os|kernel|runner|pkg|verified|baked)=?' > "$STATE_DIR/manifests/linux-$VERSION.txt")
printf '%s\n' "$output" | grep -q "^baked runner=${RUNNER_VERSION}" || { printf '%s\n' "$output" | tail -15 >&2; fail "the bake did not finish; nothing was captured"; }

printf '== deprovisioning (the VM shuts itself down)\n'
printf "nohup sh -c 'sleep 5; waagent -deprovision+user -force; poweroff' >/dev/null 2>&1 &\n" > "$WORK/deprovision.sh"
run_script "$BAKE_VM" "$WORK/deprovision.sh" >/dev/null || true
state=""
for _ in $(seq 1 60); do
  state="$(az vm get-instance-view -g "$RESOURCE_GROUP" -n "$BAKE_VM" --only-show-errors \
    --query "instanceView.statuses[?starts_with(code,'PowerState')].displayStatus | [0]" -o tsv || true)"
  [[ "$state" == "VM stopped" || "$state" == "VM deallocated" ]] && break
  sleep 10
done
[[ "$state" == "VM stopped" || "$state" == "VM deallocated" ]] || fail "the template VM did not shut down (state: ${state:-unknown})"

printf '== generalizing and capturing\n'
az vm deallocate -g "$RESOURCE_GROUP" -n "$BAKE_VM" --only-show-errors
az vm generalize -g "$RESOURCE_GROUP" -n "$BAKE_VM" --only-show-errors
vm_id="$(az vm show -g "$RESOURCE_GROUP" -n "$BAKE_VM" --query id -o tsv --only-show-errors)"
az sig image-version create -g "$RESOURCE_GROUP" --gallery-name "$GALLERY" --gallery-image-definition "$IMAGE" \
  --gallery-image-version "$VERSION" --virtual-machine "$vm_id" --replica-count 1 \
  --storage-account-type Standard_LRS --only-show-errors -o none
CAPTURED=1
image_id="$(az sig image-version show -g "$RESOURCE_GROUP" --gallery-name "$GALLERY" --gallery-image-definition "$IMAGE" \
  --gallery-image-version "$VERSION" --query id -o tsv --only-show-errors)"
install -d -m 700 "$STATE_DIR"
(umask 077; printf '%s\n' "$image_id" > "$STATE_DIR/linux-image-version.txt")
printf 'Image version recorded in %s/linux-image-version.txt\n' "$STATE_DIR"
printf '== deleting the template VM\n'
delete_vm "$BAKE_VM"
}

if (( VERIFY_ONLY == 1 )); then
  [[ -f "$STATE_DIR/linux-image-version.txt" ]] || fail "no Linux image is recorded in $STATE_DIR/linux-image-version.txt"
  image_id="$(tr -d '[:space:]' < "$STATE_DIR/linux-image-version.txt")"
  CAPTURED=1
  VERIFY=1
else
  bake_image
fi

if (( VERIFY == 1 )); then
  printf '== verifying: a throwaway VM from the new image, started the way the broker starts one\n'
  # The broker's cloud-init script shape with a base64-looking value that is NOT a runner configuration: the runner
  # starts, fails to use it and exits, and no runner can register with GitHub.
  printf "#!/bin/bash\nset -eu\ncd /opt/actions-runner\nexec runuser -u runner -- ./run.sh --jitconfig 'bm90LWEtcmVhbC1ydW5uZXItY29uZmlndXJhdGlvbg=='\n" > "$WORK/userdata.sh"
  create_vm "$VERIFY_VM" "$image_id" "$WORK/userdata.sh"
  wait_for_agent "$VERIFY_VM"
  sleep 30
  cat > "$WORK/check.sh" <<'CHECK'
set -u
echo "user=$(id -un runner 2>&1)"
echo "docker=$(docker --version 2>&1)"
echo "git=$(git --version 2>&1)"
echo "runner=$(runuser -u runner -- /opt/actions-runner/bin/Runner.Listener --version 2>&1)"
echo "sudo=$(runuser -u runner -- sudo -n true 2>&1 && echo ok)"
echo "docker-run=$(runuser -u runner -- docker info --format '{{.ServerVersion}}' 2>&1 | head -1)"
echo "runner-logs=$(ls /opt/actions-runner/_diag 2>/dev/null | wc -l)"
echo "cloud-init=$(cloud-init status 2>&1 | head -1)"
CHECK
  check="$(run_script "$VERIFY_VM" "$WORK/check.sh")"
  printf '%s\n' "$check" | grep -E '^(user|docker|git|runner|sudo|docker-run|runner-logs|cloud-init)=' || true
  printf '%s\n' "$check" | grep -q '^sudo=ok' || fail "verification: sudo does not work on the new image"
  printf '%s\n' "$check" | grep -q "^runner=${RUNNER_VERSION}" || fail "verification: the runner did not report ${RUNNER_VERSION}"
  printf '%s\n' "$check" | grep -Eq '^runner-logs=[1-9]' || fail "verification: the cloud-init script never started the runner"
  printf 'verified: the image boots, runs the broker-style start script and has the tools\n'
  delete_vm "$VERIFY_VM"
fi
printf 'Done. Nothing was deployed; the broker'"'"'s Linux class is still off.\n'
