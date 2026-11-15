#!/usr/bin/env bash
# Create or delete the billable Azure runner pilot.
#
# Terraform state lives under the owner-only XDG state directory, never in
# Git. The module does not install k3s or Actions Runner Controller. After
# apply, run the existing install helpers. destroy deletes the resource group,
# including the disk and public IP that keep billing after a VM stop.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="${REPO_ROOT}/infra/azure-runner/terraform"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-runner"
STATE_FILE="${STATE_DIR}/terraform.tfstate"
STATE_BACKUP="${STATE_DIR}/terraform.tfstate.backup"
SNAPSHOT_DIR="${STATE_DIR}/pre-adopt"
KEY_FILE="${AZURE_RUNNER_SSH_PUBLIC_KEY_FILE:-${HOME}/.ssh/id_ed25519.pub}"
trap 'rm -f "${STATE_DIR}/apply.tfplan"; chmod 600 "${STATE_DIR}/terraform.tfstate" "${STATE_DIR}/terraform.tfstate.backup" 2>/dev/null || true' EXIT
RESOURCE_GROUP="rg-arc-pilot"
NAME_PREFIX="arcpilot"

usage() {
  cat <<'EOF'
Usage: azure_runner_stack.sh <plan|apply|destroy|adopt>

  plan     Show what apply or destroy would change. Creates nothing.
  apply    Create the pilot resource group and VM. Type APPLY to confirm.
  destroy  Delete the pilot resource group, disk, and public IP. Type DESTROY.
  adopt    Import the already deployed pilot resources into local state.

State is stored outside the repository. The active Azure CLI account selects
the subscription. No subscription id, private key, or GitHub token is written
into the repository.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
[[ "$#" == "1" ]] || fail "choose plan, apply, destroy, or adopt; use --help"
ACTION="$1"
case "$ACTION" in
  plan | apply | destroy | adopt) ;;
  *) fail "unknown action: ${ACTION}; use --help" ;;
esac

for command_name in terraform az python3 install stat find; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command not found: ${command_name}"
done

[[ -d "$MODULE" && ! -L "$MODULE" ]] || fail "Terraform module is missing: ${MODULE}"

install -d -m 700 "$STATE_DIR"
[[ "$(stat -c '%u' "$STATE_DIR")" == "$(id -u)" ]] \
  || fail "state directory must be owned by the current user"
[[ "$(stat -c '%a' "$STATE_DIR")" == "700" ]] \
  || fail "state directory must have mode 0700"
if find "$STATE_DIR" -type l -print -quit | grep -q .; then
  fail "state directory must not contain symlinks"
fi

[[ -f "$KEY_FILE" && ! -L "$KEY_FILE" ]] \
  || fail "SSH public key file is missing or is a symlink: ${KEY_FILE}"
[[ "$(stat -c '%u' "$KEY_FILE")" == "$(id -u)" ]] \
  || fail "SSH public key file must be owned by the current user"
if grep -q 'PRIVATE KEY' "$KEY_FILE"; then
  fail "SSH key file contains a private key; pass the public key only"
fi

# read trims surrounding whitespace. A trailing space is not part of the key.
read -r ADMIN_SSH_PUBLIC_KEY < "$KEY_FILE"
[[ -n "$ADMIN_SSH_PUBLIC_KEY" ]] || fail "SSH public key file is empty"

SUBSCRIPTION_ID="$(az account show --query id --output tsv --only-show-errors)"
[[ "$SUBSCRIPTION_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] \
  || fail "Azure CLI does not have an active subscription"

export TF_IN_AUTOMATION=1
export TF_VAR_subscription_id="$SUBSCRIPTION_ID"
export TF_VAR_admin_ssh_public_key="$ADMIN_SSH_PUBLIC_KEY"
export ARM_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"

terraform_cmd() {
  terraform -chdir="$MODULE" "$@"
}

terraform_cmd init -input=false -reconfigure -backend-config="path=${STATE_DIR}/terraform.tfstate" >/dev/null

resource_id() {
  local relative="$1"
  printf '/subscriptions/%s/resourceGroups/%s%s' \
    "$SUBSCRIPTION_ID" "$RESOURCE_GROUP" "$relative"
}

import_one() {
  local address="$1"
  local id="$2"
  if terraform_cmd state show "$address" >/dev/null 2>&1; then
    printf 'already adopted: %s\n' "$address"
    return 0
  fi
  terraform_cmd import -input=false "$address" "$id" >/dev/null || return 1
  printf 'adopted: %s\n' "$address"
}

adopt_all() {
  import_one azurerm_resource_group.pilot "$(resource_id "")" || return 1
  import_one azurerm_network_security_group.pilot \
    "$(resource_id "/providers/Microsoft.Network/networkSecurityGroups/${NAME_PREFIX}-nsg")" || return 1
  import_one azurerm_virtual_network.pilot \
    "$(resource_id "/providers/Microsoft.Network/virtualNetworks/${NAME_PREFIX}-vnet")" || return 1
  import_one azurerm_subnet.pilot \
    "$(resource_id "/providers/Microsoft.Network/virtualNetworks/${NAME_PREFIX}-vnet/subnets/${NAME_PREFIX}-subnet")" || return 1
  import_one azurerm_subnet_network_security_group_association.pilot \
    "$(resource_id "/providers/Microsoft.Network/virtualNetworks/${NAME_PREFIX}-vnet/subnets/${NAME_PREFIX}-subnet")" || return 1
  import_one azurerm_public_ip.pilot \
    "$(resource_id "/providers/Microsoft.Network/publicIPAddresses/${NAME_PREFIX}-pip")" || return 1
  import_one azurerm_network_interface.pilot \
    "$(resource_id "/providers/Microsoft.Network/networkInterfaces/${NAME_PREFIX}-nic")" || return 1
  import_one azurerm_linux_virtual_machine.pilot \
    "$(resource_id "/providers/Microsoft.Compute/virtualMachines/${NAME_PREFIX}-vm")" || return 1
}

snapshot_state() {
  rm -rf "$SNAPSHOT_DIR"
  install -d -m 700 "$SNAPSHOT_DIR"
  if [[ -f "$STATE_FILE" ]]; then
    cp -a "$STATE_FILE" "${SNAPSHOT_DIR}/terraform.tfstate"
  fi
  if [[ -f "$STATE_BACKUP" ]]; then
    cp -a "$STATE_BACKUP" "${SNAPSHOT_DIR}/terraform.tfstate.backup"
  fi
}

restore_state() {
  if [[ -f "${SNAPSHOT_DIR}/terraform.tfstate" ]]; then
    cp -a "${SNAPSHOT_DIR}/terraform.tfstate" "$STATE_FILE"
  else
    rm -f "$STATE_FILE"
  fi
  if [[ -f "${SNAPSHOT_DIR}/terraform.tfstate.backup" ]]; then
    cp -a "${SNAPSHOT_DIR}/terraform.tfstate.backup" "$STATE_BACKUP"
  else
    rm -f "$STATE_BACKUP"
  fi
  rm -rf "$SNAPSHOT_DIR"
}

plan_has_replacement() {
  local plan_file="$1"
  local found
  if ! found="$(terraform_cmd show -json "$plan_file" | python3 -c '
import json, sys
plan = json.load(sys.stdin)
found = []
for change in plan.get("resource_changes") or []:
    actions = (change.get("change") or {}).get("actions") or []
    if "delete" in actions and "create" in actions:
        found.append(change.get("address") or "unknown")
if found:
    print("\n".join(found))
')"; then
    return 2
  fi
  if [[ -z "$found" ]]; then
    return 1
  fi
  printf 'Plan would replace:\n%s\n' "$found" >&2
  return 0
}

confirm() {
  local expected="$1"
  printf 'Type %s to continue: ' "$expected"
  local answer
  read -r answer
  [[ "$answer" == "$expected" ]] || fail "operation cancelled"
}

case "$ACTION" in
  plan)
    terraform_cmd plan -input=false -lock-timeout=2m
    ;;
  apply)
    printf 'This creates billable resources in %s (%s).\n' \
      "$RESOURCE_GROUP" "northcentralus"
    printf 'The VM, managed disk, and static public IP all bill until destroy.\n'
    plan_file="${STATE_DIR}/apply.tfplan"
    plan_status=0
    terraform_cmd plan -input=false -lock-timeout=2m -detailed-exitcode -out="$plan_file" \
      || plan_status=$?
    if [[ -f "$plan_file" ]]; then
      chmod 600 "$plan_file"
    fi
    case "$plan_status" in
      0)
        rm -f "$plan_file"
        printf 'No changes. The pilot stack already matches this configuration.\n'
        exit 0
        ;;
      2) ;;
      *)
        rm -f "$plan_file"
        fail "terraform plan failed"
        ;;
    esac
    replace_status=0
    plan_has_replacement "$plan_file" || replace_status=$?
    case "$replace_status" in
      0)
        if [[ "${AZURE_RUNNER_ALLOW_REPLACE:-}" != "1" ]]; then
          rm -f "$plan_file"
          fail "plan would replace an existing resource. Set AZURE_RUNNER_ALLOW_REPLACE=1 only when replacement is intended."
        fi
        ;;
      1) ;;
      *)
        rm -f "$plan_file"
        fail "could not read the Terraform plan"
        ;;
    esac
    confirm APPLY
    terraform_cmd apply -input=false -lock-timeout=10m "$plan_file"
    rm -f "$plan_file"
    ;;
  destroy)
    printf 'This deletes %s, including the VM, disk, and public IP.\n' \
      "$RESOURCE_GROUP"
    printf 'k3s and the runner installation on that VM are removed with it.\n'
    confirm DESTROY
    terraform_cmd destroy -input=false -auto-approve -lock-timeout=15m
    ;;
  adopt)
    snapshot_state
    if ! adopt_all; then
      restore_state
      fail "adopt failed; local state was restored. No Azure resource was changed."
    fi
    rm -rf "$SNAPSHOT_DIR"
    printf 'Adopted the existing pilot. Review plan before apply.\n'
    ;;
esac
