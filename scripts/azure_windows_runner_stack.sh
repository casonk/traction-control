#!/usr/bin/env bash
# Create or delete the billable Azure Windows runner VM.
#
# Terraform state lives under the owner-only XDG state directory, never in
# Git. The generated administrator password is kept only in that state. The
# module creates the VM and nothing else: it installs no runner and opens no
# inbound port. destroy deletes the resource group, including the disk and
# public IP that keep billing after a VM stop. This stack is separate from the
# Linux runner pool (azure_runner_stack.sh) and can be created or destroyed alone.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="${REPO_ROOT}/infra/azure-windows-runner/terraform"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-windows-runner"
STATE_FILE="${STATE_DIR}/terraform.tfstate"
STATE_BACKUP="${STATE_DIR}/terraform.tfstate.backup"
trap 'rm -f "${STATE_DIR}/apply.tfplan"; chmod 600 "${STATE_DIR}/terraform.tfstate" "${STATE_DIR}/terraform.tfstate.backup" 2>/dev/null || true' EXIT
RESOURCE_GROUP="rg-win-runner"

usage() {
  cat <<'EOF'
Usage: azure_windows_runner_stack.sh <plan|apply|destroy>

  plan     Show what apply or destroy would change. Creates nothing.
  apply    Create the Windows runner resource group and VM. Type APPLY to confirm.
  destroy  Delete the resource group, disk, and public IP. Type DESTROY.

State is stored outside the repository. The active Azure CLI account selects
the subscription. No subscription id, password, or GitHub token is written
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
[[ "$#" == "1" ]] || fail "choose plan, apply, or destroy; use --help"
ACTION="$1"
case "$ACTION" in
  plan | apply | destroy) ;;
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


SUBSCRIPTION_ID="$(az account show --query id --output tsv --only-show-errors)"
[[ "$SUBSCRIPTION_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] \
  || fail "Azure CLI does not have an active subscription"

export TF_IN_AUTOMATION=1
export TF_VAR_subscription_id="$SUBSCRIPTION_ID"
export ARM_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"

terraform_cmd() {
  terraform -chdir="$MODULE" "$@"
}

terraform_cmd init -input=false -reconfigure -backend-config="path=${STATE_DIR}/terraform.tfstate" >/dev/null


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
    printf 'The VM, managed disk, and static public IP all bill until destroy. The VM\nauto-shuts down daily (compute stops; disk and IP keep billing).\n'
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
        printf 'No changes. The Windows runner stack already matches this configuration.\n'
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
        if [[ "${AZURE_WINDOWS_RUNNER_ALLOW_REPLACE:-}" != "1" ]]; then
          rm -f "$plan_file"
          fail "plan would replace an existing resource. Set AZURE_WINDOWS_RUNNER_ALLOW_REPLACE=1 only when replacement is intended."
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
    printf 'Anything installed on that VM is removed with it.\n'
    confirm DESTROY
    terraform_cmd destroy -input=false -auto-approve -lock-timeout=15m
    ;;
esac
