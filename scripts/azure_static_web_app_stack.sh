#!/usr/bin/env bash
# Create or delete one Azure Static Web App.
#
# Terraform state lives under an owner-only directory, never in Git. That
# state includes the site's deployment token. This wrapper does not upload
# HTML and does not connect a Git repository.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: azure_static_web_app_stack.sh <plan|apply|destroy|adopt>

  plan     Show what apply or destroy would change. Creates nothing.
  apply    Create the resource group and Free or Standard site. Type APPLY.
  destroy  Delete the resource group and the site. Type DESTROY.
  adopt    Import an already deployed site into local state.

Required:
  AZURE_SWA_STACK        Short lowercase name. State defaults to
                         ~/.local/share/traction-control/azure-swa/<stack>
                         and the resource group defaults to rg-<stack>.
  AZURE_SWA_NAME         Globally unique site name, 1 to 60 letters, digits,
                         or interior hyphens.

Optional:
  AZURE_SWA_LOCATION     centralus, eastus2, westus2, westeurope, or eastasia.
                         Default: centralus. northcentralus is rejected.
  AZURE_SWA_SKU          Free or Standard. Default: Free.
  AZURE_SWA_RESOURCE_GROUP
  AZURE_SWA_STATE_DIR    Absolute owner-only directory for state.
  AZURE_SWA_PURPOSE      Purpose tag. Default: static-web-app.
  AZURE_SWA_LIFECYCLE    Lifecycle tag. Default: free-static-web-app.
  AZURE_SWA_ALLOW_REPLACE
                         Set to 1 to apply a plan that replaces the site.
                         A name, region, or resource-group change is a
                         replacement and issues a new hostname.

The active Azure CLI account selects the subscription. No subscription id
or deployment token is written into the repository.
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

STACK="${AZURE_SWA_STACK:-}"
[[ "$STACK" =~ ^[a-z0-9][a-z0-9-]{0,22}$ ]] \
  || fail "set AZURE_SWA_STACK to a short lowercase name"

NAME="${AZURE_SWA_NAME:-}"
[[ "$NAME" =~ ^[0-9A-Za-z]([0-9A-Za-z-]{0,58}[0-9A-Za-z])?$ ]] \
  || fail "set AZURE_SWA_NAME to 1-60 letters, digits, or interior hyphens"

LOCATION="${AZURE_SWA_LOCATION:-centralus}"
case "$LOCATION" in
  centralus | eastus2 | westus2 | westeurope | eastasia) ;;
  *) fail "location must be centralus, eastus2, westus2, westeurope, or eastasia" ;;
esac

SKU="${AZURE_SWA_SKU:-Free}"
case "$SKU" in
  Free | Standard) ;;
  *) fail "AZURE_SWA_SKU must be Free or Standard" ;;
esac

if [[ -n "${AZURE_SWA_STATE_DIR:-}" ]]; then
  STATE_DIR="$AZURE_SWA_STATE_DIR"
else
  STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-swa/${STACK}"
fi
[[ "$STATE_DIR" == /* ]] || fail "state directory must be an absolute path"

if [[ -n "${AZURE_SWA_RESOURCE_GROUP:-}" ]]; then
  RESOURCE_GROUP="$AZURE_SWA_RESOURCE_GROUP"
else
  RESOURCE_GROUP="rg-${STACK}"
fi
[[ "$RESOURCE_GROUP" =~ ^[A-Za-z0-9._()-]{1,90}$ ]] \
  || fail "resource group name is not a single Azure name"

PURPOSE="${AZURE_SWA_PURPOSE:-static-web-app}"
LIFECYCLE_TAG="${AZURE_SWA_LIFECYCLE:-free-static-web-app}"
[[ "$PURPOSE" =~ ^[A-Za-z0-9._:-]{1,64}$ ]] || fail "purpose tag is empty or has unsupported characters"
[[ "$LIFECYCLE_TAG" =~ ^[A-Za-z0-9._:-]{1,64}$ ]] || fail "lifecycle tag is empty or has unsupported characters"

MODULE="${AZURE_SWA_MODULE:-${REPO_ROOT}/infra/azure-static-web-app-stack}"
[[ "$MODULE" == /* && -d "$MODULE" && ! -L "$MODULE" ]] \
  || fail "Terraform module is missing: ${MODULE}"

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

SUBSCRIPTION_ID="$(az account show --query id --output tsv --only-show-errors)"
[[ "$SUBSCRIPTION_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] \
  || fail "Azure CLI does not have an active subscription"

export TF_IN_AUTOMATION=1
export TF_VAR_subscription_id="$SUBSCRIPTION_ID"
export TF_VAR_resource_group_name="$RESOURCE_GROUP"
export TF_VAR_location="$LOCATION"
export TF_VAR_name="$NAME"
export TF_VAR_sku="$SKU"
export TF_VAR_purpose_tag="$PURPOSE"
export TF_VAR_lifecycle_tag="$LIFECYCLE_TAG"
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
  import_one module.site.azurerm_resource_group.site "$(resource_id "")" || return 1
  import_one module.site.azurerm_static_web_app.site \
    "$(resource_id "/providers/Microsoft.Web/staticSites/${NAME}")" || return 1
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
    printf 'Static Web App %s (%s) in %s (%s).\n' "$NAME" "$SKU" "$RESOURCE_GROUP" "$LOCATION"
    terraform_cmd plan -input=false -lock-timeout=2m
    ;;
  apply)
    printf 'This creates or updates %s (%s) in %s (%s).\n' \
      "$NAME" "$SKU" "$RESOURCE_GROUP" "$LOCATION"
    printf 'Free has no standing compute charge. Standard is billed per app.\n'
    printf 'Changing the name, region, or resource group replaces the site.\n'
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
        if [[ "${AZURE_SWA_ALLOW_REPLACE:-}" != "1" ]]; then
          rm -f "$plan_file"
          fail "plan would replace an existing resource. Set AZURE_SWA_ALLOW_REPLACE=1 only when replacement is intended."
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
    terraform_cmd output -raw default_host_name
    printf '\n'
    ;;
  destroy)
    printf 'This deletes %s, including %s and its hostname.\n' "$RESOURCE_GROUP" "$NAME"
    printf 'Destroy removes the public site. Free has no standing charge to stop.\n'
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
