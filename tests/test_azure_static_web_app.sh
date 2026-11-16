#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/azure_static_web_app_stack.sh"
MODULE="${REPO_ROOT}/infra/azure-static-web-app"
ROOT="${REPO_ROOT}/infra/azure-static-web-app-stack"

failures=0

ok() {
  printf 'ok - %s\n' "$1"
}

not_ok() {
  printf 'not ok - %s\n' "$1"
  failures=$((failures + 1))
}

check_status() {
  local expected="$1"
  local label="$2"
  shift 2
  local status=0
  "$@" >/dev/null 2>&1 || status=$?
  if [[ "$status" == "$expected" ]]; then
    ok "$label"
  else
    not_ok "${label} (expected ${expected}, got ${status})"
  fi
}

require_text() {
  local file="$1"
  local text="$2"
  local label="$3"
  if grep -Fq "$text" "$file"; then
    ok "$label"
  else
    not_ok "$label"
  fi
}

require_absent() {
  local file="$1"
  local text="$2"
  local label="$3"
  if grep -Fq "$text" "$file"; then
    not_ok "$label"
  else
    ok "$label"
  fi
}

[[ -x "$SCRIPT" ]] && ok "wrapper is executable" || not_ok "wrapper is executable"
check_status 0 "help exits 0" "$SCRIPT" --help
check_status 1 "missing stack fails closed" \
  env AZURE_SWA_NAME=grocery-demo "$SCRIPT" plan
check_status 1 "northcentralus fails closed" \
  env AZURE_SWA_STACK=grocery AZURE_SWA_NAME=grocery-demo AZURE_SWA_LOCATION=northcentralus \
  "$SCRIPT" plan
check_status 1 "dedicated sku fails closed" \
  env AZURE_SWA_STACK=grocery AZURE_SWA_NAME=grocery-demo AZURE_SWA_SKU=Dedicated \
  "$SCRIPT" plan
check_status 1 "bad site name fails closed" \
  env AZURE_SWA_STACK=grocery AZURE_SWA_NAME='grocery_list' "$SCRIPT" plan

location_line="$(grep -n 'northcentralus is rejected' "$SCRIPT" | head -1 | cut -d: -f1)"
az_line="$(grep -n 'az account show' "$SCRIPT" | head -1 | cut -d: -f1)"
if [[ -n "$location_line" && -n "$az_line" && "$location_line" -lt "$az_line" ]]; then
  ok "region is checked before Azure"
else
  not_ok "region is checked before Azure"
fi

for file in versions.tf variables.tf main.tf outputs.tf; do
  [[ -f "${MODULE}/${file}" ]] && ok "module has ${file}" || not_ok "module has ${file}"
  [[ -f "${ROOT}/${file}" ]] && ok "root has ${file}" || not_ok "root has ${file}"
done

require_text "${ROOT}/main.tf" 'source = "../azure-static-web-app"' "root calls the shared module"
require_text "${MODULE}/main.tf" 'resource "azurerm_static_web_app" "site"' "module declares the site"
require_text "${MODULE}/main.tf" 'sku_tier = var.sku' "tier follows the selected sku"
require_text "${MODULE}/main.tf" 'sku_size = var.sku' "size follows the selected sku"
require_text "${MODULE}/variables.tf" 'default     = "Free"' "sku defaults to Free"
require_text "${MODULE}/variables.tf" 'default     = "centralus"' "region defaults to centralus"
require_text "${ROOT}/versions.tf" 'backend "local" {}' "state backend is local"
require_text "${ROOT}/versions.tf" 'prevent_deletion_if_contains_resources = false' "group deletion removes remaining resources"
require_absent "${MODULE}/versions.tf" 'backend "local"' "module has no backend"
require_text "${ROOT}/.terraform.lock.hcl" 'version     = "4.81.0"' "provider lock stays on AzureRM 4.81.0"
require_text "$SCRIPT" 'confirm APPLY' "apply requires the APPLY confirmation"
require_text "$SCRIPT" 'confirm DESTROY' "destroy requires the DESTROY confirmation"
require_text "$SCRIPT" 'AZURE_SWA_ALLOW_REPLACE' "apply refuses an unconfirmed replacement"
require_text "$SCRIPT" 'local state was restored' "a failed adopt restores local state"
require_text "$SCRIPT" 'module.site.azurerm_static_web_app.site' "adopt addresses the site"
require_absent "${MODULE}/outputs.tf" 'output "api_key"' "deployment token is not an output"
require_absent "${ROOT}/outputs.tf" 'output "api_key"' "root does not publish the deployment token"
require_absent "${MODULE}/main.tf" 'repository_token' "module does not accept a repository token"
require_absent "$SCRIPT" 'repository_token' "wrapper does not accept a repository token"
if grep -Eq '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' "$SCRIPT"; then
  not_ok "wrapper contains no subscription id"
else
  ok "wrapper contains no subscription id"
fi

if [[ "$failures" != "0" ]]; then
  printf '%s failed\n' "$failures" >&2
  exit 1
fi
printf 'azure static web app checks passed\n'
