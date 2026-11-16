#!/usr/bin/env bash
# Create or delete one billable Azure VM stack.
#
# Terraform state lives under an owner-only directory, never in Git. This
# wrapper does not install k3s or any workload. destroy deletes the resource
# group, including the disk and public IP that keep billing after a VM stop.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: azure_vm_stack.sh <plan|apply|destroy|adopt>

  plan     Show what apply or destroy would change. Creates nothing.
  apply    Create the resource group and VM. Type APPLY to confirm.
  destroy  Delete the resource group, disk, and public IP. Type DESTROY.
  adopt    Import an already deployed stack into local state.

Required:
  AZURE_VM_SIZE          Azure size, such as Standard_B2als_v2.

Identity, when AZURE_VM_STATE_DIR is not set by a caller:
  AZURE_VM_STACK         Short lowercase name. State defaults to
                         ~/.local/share/traction-control/azure-vm/<stack>
                         and the resource group defaults to rg-<stack>.
                         The name prefix defaults to the stack only when
                         the stack is 3 to 8 lowercase letters or digits.

Optional:
  AZURE_VM_MODULE        Terraform root. Default: infra/azure-vm-stack.
  AZURE_VM_LOCATION      Region. Default: northcentralus.
  AZURE_VM_NAME_PREFIX   3 to 8 lowercase letters or digits.
  AZURE_VM_ADDRESS_SPACE VNet prefix. Default: 10.240.0.0/16.
  AZURE_VM_SUBNET_PREFIX Subnet prefix. Default: 10.240.1.0/24.
  AZURE_VM_PUBLIC_TCP_PORTS
                         Comma-separated inbound TCP ports. Empty denies
                         every inbound connection. A public website uses
                         80,443. See docs/templates/azure-small-website/.
  AZURE_VM_ALLOW_REPLACE Set to 1 to apply a plan that replaces a resource.
                         A VM size change is a replacement.

The active Azure CLI account selects the subscription. No subscription id,
private key, or token is written into the repository.
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

# Reject a bad size before any Azure or Terraform command.
VM_SIZE="${AZURE_VM_SIZE:-}"
[[ "$VM_SIZE" =~ ^Standard_[A-Za-z0-9_]{1,40}$ ]] \
  || fail "set AZURE_VM_SIZE to an Azure size such as Standard_B2als_v2"

STACK="${AZURE_VM_STACK:-}"
if [[ -n "$STACK" && ! "$STACK" =~ ^[a-z0-9][a-z0-9-]{0,22}$ ]]; then
  fail "AZURE_VM_STACK must be a short lowercase name"
fi

if [[ -n "${AZURE_VM_STATE_DIR:-}" ]]; then
  STATE_DIR="$AZURE_VM_STATE_DIR"
elif [[ -n "$STACK" ]]; then
  STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-vm/${STACK}"
else
  fail "set AZURE_VM_STACK or AZURE_VM_STATE_DIR"
fi
[[ "$STATE_DIR" == /* ]] || fail "state directory must be an absolute path"

if [[ -n "${AZURE_VM_RESOURCE_GROUP:-}" ]]; then
  RESOURCE_GROUP="$AZURE_VM_RESOURCE_GROUP"
elif [[ -n "$STACK" ]]; then
  RESOURCE_GROUP="rg-${STACK}"
else
  fail "set AZURE_VM_RESOURCE_GROUP or AZURE_VM_STACK"
fi
[[ "$RESOURCE_GROUP" =~ ^[A-Za-z0-9._()-]{1,90}$ ]] \
  || fail "resource group name is not a single Azure name"

if [[ -n "${AZURE_VM_NAME_PREFIX:-}" ]]; then
  NAME_PREFIX="$AZURE_VM_NAME_PREFIX"
elif [[ "$STACK" =~ ^[a-z0-9]{3,8}$ ]]; then
  NAME_PREFIX="$STACK"
else
  fail "set AZURE_VM_NAME_PREFIX to 3-8 lowercase letters or digits"
fi
[[ "$NAME_PREFIX" =~ ^[a-z0-9]{3,8}$ ]] \
  || fail "name prefix must be 3-8 lowercase letters or digits"

LOCATION="${AZURE_VM_LOCATION:-northcentralus}"
[[ "$LOCATION" =~ ^[a-z0-9]{1,40}$ ]] \
  || fail "location must be an Azure region name such as northcentralus"

ADMIN_USERNAME="${AZURE_VM_ADMIN_USERNAME:-nodeadmin}"
[[ "$ADMIN_USERNAME" =~ ^[a-z][a-z0-9-]{0,31}$ ]] \
  || fail "admin username must be a Linux account name"

ADDRESS_SPACE="${AZURE_VM_ADDRESS_SPACE:-10.240.0.0/16}"
SUBNET_PREFIX="${AZURE_VM_SUBNET_PREFIX:-10.240.1.0/24}"
cidr_re='^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$'
[[ "$ADDRESS_SPACE" =~ $cidr_re ]] || fail "address space must be an IPv4 CIDR"
[[ "$SUBNET_PREFIX" =~ $cidr_re ]] || fail "subnet prefix must be an IPv4 CIDR"

PUBLIC_PORTS_JSON="[]"
if [[ -n "${AZURE_VM_PUBLIC_TCP_PORTS:-}" ]]; then
  IFS=',' read -r -a public_port_list <<< "${AZURE_VM_PUBLIC_TCP_PORTS}"
  [[ "${#public_port_list[@]}" -le 20 ]] || fail "too many public TCP ports"
  PUBLIC_PORTS_JSON="["
  public_port_sep=""
  declare -A public_port_seen=()
  for public_port in "${public_port_list[@]}"; do
    [[ "$public_port" =~ ^[0-9]{1,5}$ && "$public_port" -ge 1 && "$public_port" -le 65535 ]] \
      || fail "public TCP port is invalid: ${public_port}"
    [[ -z "${public_port_seen[$public_port]:-}" ]] || fail "public TCP port is repeated: ${public_port}"
    public_port_seen["$public_port"]=1
    PUBLIC_PORTS_JSON+="${public_port_sep}${public_port}"
    public_port_sep=","
  done
  PUBLIC_PORTS_JSON+="]"
fi

PURPOSE="${AZURE_VM_PURPOSE:-shared-linux-node}"
LIFECYCLE_TAG="${AZURE_VM_LIFECYCLE:-destroy-to-stop-billing}"
[[ "$PURPOSE" =~ ^[A-Za-z0-9._:-]{1,64}$ ]] || fail "purpose tag is empty or has unsupported characters"
[[ "$LIFECYCLE_TAG" =~ ^[A-Za-z0-9._:-]{1,64}$ ]] || fail "lifecycle tag is empty or has unsupported characters"

MODULE="${AZURE_VM_MODULE:-${REPO_ROOT}/infra/azure-vm-stack}"
[[ "$MODULE" == /* && -d "$MODULE" && ! -L "$MODULE" ]] \
  || fail "Terraform module is missing: ${MODULE}"
KEY_FILE="${AZURE_VM_SSH_PUBLIC_KEY_FILE:-${HOME}/.ssh/id_ed25519.pub}"
DESTROY_NOTE="${AZURE_VM_DESTROY_NOTE:-Destroy deletes this VM and everything stored on it.}"
STATE_FILE="${STATE_DIR}/terraform.tfstate"
STATE_BACKUP="${STATE_DIR}/terraform.tfstate.backup"
SNAPSHOT_DIR="${STATE_DIR}/pre-adopt"

trap 'rm -f "${STATE_DIR}/apply.tfplan"; chmod 600 "${STATE_DIR}/terraform.tfstate" "${STATE_DIR}/terraform.tfstate.backup" 2>/dev/null || true' EXIT

for command_name in terraform az python3 install stat find; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command not found: ${command_name}"
done

if [[ -L "$STATE_DIR" ]]; then
  fail "state directory must not be a symlink"
fi
install -d -m 700 "$STATE_DIR"
[[ "$(stat -c '%u' "$STATE_DIR")" == "$(id -u)" ]] \
  || fail "state directory must be owned by the current user"
[[ "$(stat -c '%a' "$STATE_DIR")" == "700" ]] \
  || fail "state directory must have mode 0700"
# Certbot stores an IP certificate as symlinks inside this directory.
# A link may stay when its resolved target also stays here. A link that
# leaves the directory is rejected so the wrapper cannot follow it.
if ! python3 - "$STATE_DIR" <<'PY'
import os
import sys

root = os.path.realpath(sys.argv[1])
for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
    for name in dirnames + filenames:
        path = os.path.join(dirpath, name)
        if not os.path.islink(path):
            continue
        target = os.path.realpath(path)
        if target != root and not target.startswith(root + os.sep):
            sys.exit(1)
PY
then
  fail "state directory symlink points outside the state directory"
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
export TF_VAR_resource_group_name="$RESOURCE_GROUP"
export TF_VAR_location="$LOCATION"
export TF_VAR_name_prefix="$NAME_PREFIX"
export TF_VAR_vm_size="$VM_SIZE"
export TF_VAR_admin_username="$ADMIN_USERNAME"
export TF_VAR_admin_ssh_public_key="$ADMIN_SSH_PUBLIC_KEY"
export TF_VAR_address_space="$ADDRESS_SPACE"
export TF_VAR_subnet_prefix="$SUBNET_PREFIX"
export TF_VAR_purpose_tag="$PURPOSE"
export TF_VAR_lifecycle_tag="$LIFECYCLE_TAG"
export TF_VAR_public_tcp_ports="$PUBLIC_PORTS_JSON"
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
  import_one module.node.azurerm_resource_group.node "$(resource_id "")" || return 1
  import_one module.node.azurerm_network_security_group.node \
    "$(resource_id "/providers/Microsoft.Network/networkSecurityGroups/${NAME_PREFIX}-nsg")" || return 1
  import_one module.node.azurerm_virtual_network.node \
    "$(resource_id "/providers/Microsoft.Network/virtualNetworks/${NAME_PREFIX}-vnet")" || return 1
  import_one module.node.azurerm_subnet.node \
    "$(resource_id "/providers/Microsoft.Network/virtualNetworks/${NAME_PREFIX}-vnet/subnets/${NAME_PREFIX}-subnet")" || return 1
  import_one module.node.azurerm_subnet_network_security_group_association.node \
    "$(resource_id "/providers/Microsoft.Network/virtualNetworks/${NAME_PREFIX}-vnet/subnets/${NAME_PREFIX}-subnet")" || return 1
  import_one module.node.azurerm_public_ip.node \
    "$(resource_id "/providers/Microsoft.Network/publicIPAddresses/${NAME_PREFIX}-pip")" || return 1
  import_one module.node.azurerm_network_interface.node \
    "$(resource_id "/providers/Microsoft.Network/networkInterfaces/${NAME_PREFIX}-nic")" || return 1
  import_one module.node.azurerm_linux_virtual_machine.node \
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
    printf 'VM size %s in %s (%s).\n' "$VM_SIZE" "$RESOURCE_GROUP" "$LOCATION"
    terraform_cmd plan -input=false -lock-timeout=2m
    ;;
  apply)
    printf 'This creates billable resources in %s (%s), VM size %s.\n' \
      "$RESOURCE_GROUP" "$LOCATION" "$VM_SIZE"
    printf 'The VM, managed disk, and static public IP all bill until destroy.\n'
    printf 'Changing the size of an existing VM replaces that VM.\n'
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
        printf 'No changes. The stack already matches this configuration.\n'
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
        if [[ "${AZURE_VM_ALLOW_REPLACE:-}" != "1" ]]; then
          rm -f "$plan_file"
          fail "plan would replace an existing resource. Set AZURE_VM_ALLOW_REPLACE=1 only when replacement is intended."
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
    printf 'This deletes %s, including the VM (%s), disk, and public IP.\n' \
      "$RESOURCE_GROUP" "$VM_SIZE"
    printf '%s\n' "$DESTROY_NOTE"
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
    printf 'Adopted the existing resources. Review plan before apply.\n'
    ;;
esac
