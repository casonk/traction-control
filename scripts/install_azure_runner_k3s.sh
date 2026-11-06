#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="rg-arc-pilot"
VM_NAME="arcpilot-vm"
AZURE_CLI_IMAGE="mcr.microsoft.com/azure-cli:2.91.0"
AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-${HOME}/.azure}"

usage() {
  cat <<'EOF'
Usage: install_azure_runner_k3s.sh

Install k3s on the deployed rg-arc-pilot/arcpilot-vm using Azure VM Run
Command. Requires Podman, the Azure CLI container image, and an owner-only
Azure CLI config directory at ~/.azure (or AZURE_CONFIG_DIR).
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
[[ "$#" == "0" ]] || fail "this script does not accept arguments; use --help"

for command_name in podman stat find grep; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command not found: ${command_name}"
done

[[ -d "$AZURE_CONFIG_DIR" && ! -L "$AZURE_CONFIG_DIR" ]] \
  || fail "Azure CLI config directory is missing or is a symlink: ${AZURE_CONFIG_DIR}"
[[ "$(stat -c '%u' "$AZURE_CONFIG_DIR")" == "$(id -u)" ]] \
  || fail "Azure CLI config directory must be owned by the current user"
[[ "$(stat -c '%a' "$AZURE_CONFIG_DIR")" == "700" ]] \
  || fail "Azure CLI config directory must have mode 0700"
if find "$AZURE_CONFIG_DIR" -type l -print -quit | grep -q .; then
  fail "Azure CLI config directory must not contain symlinks"
fi
unsafe_path="$(find "$AZURE_CONFIG_DIR" \( -type f -o -type d \) -perm /077 -print -quit)" \
  || fail "could not validate Azure CLI config permissions"
if [[ -n "$unsafe_path" ]]; then
  fail "Azure CLI files and directories must not grant group or other permissions"
fi

podman image exists "$AZURE_CLI_IMAGE" \
  || fail "Azure CLI image is not available locally: ${AZURE_CLI_IMAGE}"

cat <<EOF
This will install or verify k3s on Azure VM ${RESOURCE_GROUP}/${VM_NAME}.
Azure Run Command executes with administrator privileges in the guest.
The VM is a billable resource and remains running afterward.
EOF
printf 'Type CONTINUE to continue: '
read -r confirmation
[[ "$confirmation" == "CONTINUE" ]] || fail "operation cancelled"

export K3S_RUN_COMMAND_SCRIPT
K3S_RUN_COMMAND_SCRIPT="$(cat <<'REMOTE_SCRIPT'
set -eu
report_status() {
  result=$?
  trap - EXIT
  printf '\n__K3S_INSTALL_EXIT_STATUS=%s\n' "$result"
  exit 0
}
trap report_status EXIT
if ! systemctl cat k3s >/dev/null 2>&1; then
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl -fsSL --retry 3 --connect-timeout 15 https://get.k3s.io -o "$installer"
  sh "$installer"
else
  printf 'k3s is already installed; skipping installer\n'
fi

if ! systemctl is-active --quiet k3s; then
  printf 'error: k3s service is not active\n' >&2
  systemctl status k3s --no-pager --full || true
  journalctl -u k3s -n 40 --no-pager || true
  exit 1
fi

printf 'Waiting up to 5 minutes for Kubernetes node registration...\n'
deadline=$(( $(date +%s) + 300 ))
while :; do
  nodes="$(/usr/local/bin/kubectl get nodes -o name 2>/dev/null || true)"
  if [ -n "$nodes" ]; then
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    printf 'error: no Kubernetes nodes registered after 5 minutes\n' >&2
    journalctl -u k3s -n 60 --no-pager || true
    exit 1
  fi
  sleep 5
done

/usr/local/bin/kubectl wait --for=condition=Ready --timeout=180s nodes --all
/usr/local/bin/kubectl get nodes
REMOTE_SCRIPT
)"

podman run --rm \
  --env K3S_RUN_COMMAND_SCRIPT \
  --volume "${AZURE_CONFIG_DIR}:/source:ro,z" \
  --entrypoint /bin/sh \
  "$AZURE_CLI_IMAGE" \
  -c '
    set -eu
    state_dir="$(mktemp -d /tmp/azure-state.XXXXXX)"
    trap '\''rm -rf "$state_dir"'\'' EXIT
    cp -a /source/. "$state_dir/"
    export AZURE_CONFIG_DIR="$state_dir"

    subscription_state="$(az account show --only-show-errors --query state --output tsv)"
    [ "$subscription_state" = "Enabled" ] || {
      printf "error: Azure subscription is not enabled\n" >&2
      exit 1
    }
    vm_state="$(az vm get-instance-view \
      --resource-group rg-arc-pilot \
      --name arcpilot-vm \
      --only-show-errors \
      --query "instanceView.statuses[?starts_with(code, '\''PowerState/'\'')].displayStatus | [0]" \
      --output tsv)"
    [ "$vm_state" = "VM running" ] || {
      printf "error: Azure VM is not running (state: %s)\n" "$vm_state" >&2
      exit 1
    }

    result="$(az vm run-command invoke \
      --resource-group rg-arc-pilot \
      --name arcpilot-vm \
      --command-id RunShellScript \
      --scripts "$K3S_RUN_COMMAND_SCRIPT" \
      --only-show-errors \
      --query "value[].message" \
      --output tsv)"
    printf "%s\n" "$result" | grep -Fvx "__K3S_INSTALL_EXIT_STATUS=0" || true
    printf "%s\n" "$result" | grep -Fxq "__K3S_INSTALL_EXIT_STATUS=0" || {
      printf "error: k3s setup failed inside the VM; inspect output above\n" >&2
      exit 1
    }
  '
