#!/usr/bin/env bash
# bake_azure_windows_image.sh — turn the Azure Windows template VM into the golden runner image.
#
#   bake_azure_windows_image.sh [--dry-run]
#
# Starts the template VM (rg-win-runner/winrun-vm), installs Git for Windows and the pinned Actions runner and the
# start task on it through Run Command (config/github-runners/windows/), runs sysprep, generalizes the VM and
# captures it as a new version of the gallery image the broker creates per-job VMs from. The template VM cannot be
# used afterwards: run azure_windows_runner_stack.sh apply to remove it (the template VM is off by default; to bake,
# first create it with TF_VAR_enable_template_vm=true ./scripts/azure_windows_runner_stack.sh apply).
# The new version id is recorded under the owner-only state directory. --dry-run prints the plan only.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$HERE/../config/github-runners/windows"
RESOURCE_GROUP="rg-win-runner"
VM_NAME="winrun-vm"
GALLERY="winrungallery"
IMAGE="windows-runner"
RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
# Pinned so a rebuild matches the last image; the value is the Git for Windows release tag of the image baked on 2026-11-17.
GIT_TAG="${GIT_TAG:-v2.56.1.windows.1}"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-windows-runner"
DRY_RUN=0

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  --help|-h) sed -n 2,13p "$0"; exit 0 ;;
  *) fail "unknown argument: $1" ;;
esac

[[ "$RUNNER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "RUNNER_VERSION must look like 2.338.2"
[[ "$GIT_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\.windows\.[0-9]+$ ]] || fail "GIT_TAG must look like v2.56.1.windows.1"
for c in az base64 date; do command -v "$c" >/dev/null 2>&1 || fail "required command not found: $c"; done
[[ -f "$CONF/bake-image.ps1" && -f "$CONF/start-runner.ps1" ]] || fail "missing scripts under $CONF"

VERSION="$(date -u +%Y).$(date -u +%m%d).$(date -u +%H%M)"
if (( DRY_RUN == 0 )) && ! az vm show -g "$RESOURCE_GROUP" -n "$VM_NAME" --only-show-errors -o none 2>/dev/null; then
  fail "the template VM $RESOURCE_GROUP/$VM_NAME does not exist; create it with: TF_VAR_enable_template_vm=true ./scripts/azure_windows_runner_stack.sh apply"
fi
printf 'Template VM : %s/%s\nGallery     : %s/%s\nNew version : %s\nRunner      : %s\nGit         : %s\n' \
  "$RESOURCE_GROUP" "$VM_NAME" "$GALLERY" "$IMAGE" "$VERSION" "$RUNNER_VERSION" "$GIT_TAG"
if (( DRY_RUN == 1 )); then
  printf 'dry run: nothing was changed.\n'
  exit 0
fi

printf 'This installs software on the template VM, generalizes it (it cannot be used afterwards) and captures the image.\n'
printf 'Type BAKE to continue: '
read -r answer
[[ "$answer" == "BAKE" ]] || fail "operation cancelled"

WORK="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/bake-image.XXXXXX")"
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT
START_B64="$(base64 -w0 "$CONF/start-runner.ps1")"
{
  printf "\$RunnerVersion = '%s'\n\$GitTag = '%s'\n\$StartScriptB64 = '%s'\n" "$RUNNER_VERSION" "$GIT_TAG" "$START_B64"
  cat "$CONF/bake-image.ps1"
} > "$WORK/bake.ps1"

power_state() {
  az vm get-instance-view -g "$RESOURCE_GROUP" -n "$VM_NAME" --only-show-errors \
    --query "instanceView.statuses[?starts_with(code,'PowerState')].displayStatus | [0]" -o tsv
}

printf '== starting the template VM\n'
az vm start -g "$RESOURCE_GROUP" -n "$VM_NAME" --only-show-errors

printf '== baking (Git for Windows, the runner, the start task)\n'
output="$(az vm run-command invoke -g "$RESOURCE_GROUP" -n "$VM_NAME" --command-id RunPowerShellScript \
  --scripts @"$WORK/bake.ps1" --only-show-errors --query 'value[].message' -o tsv)"
printf '%s\n' "$output"
printf '%s\n' "$output" | grep -q "^baked runner=${RUNNER_VERSION}" || fail "the bake did not finish; the VM was not generalized"
install -d -m 700 "$STATE_DIR/manifests"
(umask 077; printf '%s\n' "$output" | grep -E '^(verified|git=|bash=|task=|baked)' > "$STATE_DIR/manifests/windows-$VERSION.txt")

printf '== sysprep (the VM shuts itself down)\n'
az vm run-command invoke -g "$RESOURCE_GROUP" -n "$VM_NAME" --command-id RunPowerShellScript --only-show-errors \
  --scripts "Start-Process -FilePath C:\\Windows\\System32\\Sysprep\\sysprep.exe -ArgumentList '/oobe','/generalize','/shutdown','/mode:vm'" \
  >/dev/null || true
for _ in $(seq 1 60); do
  state="$(power_state || true)"
  [[ "$state" == "VM stopped" || "$state" == "VM deallocated" ]] && break
  sleep 10
done
[[ "$state" == "VM stopped" || "$state" == "VM deallocated" ]] || fail "the VM did not shut down after sysprep (state: ${state:-unknown})"

printf '== generalizing and capturing\n'
az vm deallocate -g "$RESOURCE_GROUP" -n "$VM_NAME" --only-show-errors
az vm generalize -g "$RESOURCE_GROUP" -n "$VM_NAME" --only-show-errors
vm_id="$(az vm show -g "$RESOURCE_GROUP" -n "$VM_NAME" --query id -o tsv --only-show-errors)"
az sig image-version create -g "$RESOURCE_GROUP" --gallery-name "$GALLERY" --gallery-image-definition "$IMAGE" \
  --gallery-image-version "$VERSION" --virtual-machine "$vm_id" --replica-count 1 \
  --storage-account-type Standard_LRS --only-show-errors >/dev/null
image_id="$(az sig image-version show -g "$RESOURCE_GROUP" --gallery-name "$GALLERY" --gallery-image-definition "$IMAGE" \
  --gallery-image-version "$VERSION" --query id -o tsv --only-show-errors)"
install -d -m 700 "$STATE_DIR"
(umask 077; printf '%s\n' "$image_id" > "$STATE_DIR/image-version.txt")
printf 'Image version recorded in %s/image-version.txt\n' "$STATE_DIR"
printf 'Next: ./scripts/azure_windows_runner_stack.sh apply   (the template VM is off by default, so this removes the generalized VM)\n'
