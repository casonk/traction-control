#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/azure_runner_stack.sh"
MODULE="${REPO_ROOT}/infra/azure-runner/terraform"
BICEP="${REPO_ROOT}/infra/azure-runner/main.bicep"

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

require_re() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if grep -Eq "$pattern" "$file"; then
    ok "$label"
  else
    not_ok "$label"
  fi
}

require_absent() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if grep -Eq "$pattern" "$file"; then
    not_ok "$label"
  else
    ok "$label"
  fi
}

[[ -x "$SCRIPT" ]] && ok "wrapper is executable" || not_ok "wrapper is executable"
check_status 0 "help exits 0" "$SCRIPT" --help
check_status 1 "unknown action fails" "$SCRIPT" explode
check_status 1 "missing terraform fails closed" \
  env PATH="/usr/bin:/bin" "$SCRIPT" plan

for file in versions.tf variables.tf main.tf outputs.tf; do
  [[ -f "${MODULE}/${file}" ]] && ok "module has ${file}" || not_ok "module has ${file}"
done

require_re "${MODULE}/main.tf" 'name[[:space:]]*=[[:space:]]*"DenyAllInbound"' "NSG denies all inbound"
require_re "${MODULE}/main.tf" 'access[[:space:]]*=[[:space:]]*"Deny"' "NSG access is deny"
require_re "${MODULE}/main.tf" 'priority[[:space:]]*=[[:space:]]*100' "deny rule stays at priority 100"
require_re "${MODULE}/main.tf" 'size[[:space:]]*=[[:space:]]*"Standard_B2als_v2"' "VM size matches the reviewed pilot"
require_re "${MODULE}/main.tf" 'address_space[[:space:]]*=[[:space:]]*\["10\.240\.0\.0/16"\]' "VNet prefix matches the Bicep scaffold"
require_re "${MODULE}/main.tf" 'offer[[:space:]]*=[[:space:]]*"ubuntu-24_04-lts"' "image matches the Bicep scaffold"
require_re "${MODULE}/main.tf" 'disable_password_authentication[[:space:]]*=[[:space:]]*true' "password authentication stays disabled"
require_re "${MODULE}/main.tf" 'computer_name[[:space:]]*=[[:space:]]*"\$\{var\.name_prefix\}-vm"' "computer name matches the VM name"
require_text "${MODULE}/main.tf" 'ignore_changes = [source_image_reference, admin_ssh_key]' "image and key drift cannot change the VM"
require_text "${MODULE}/versions.tf" 'delete_os_disk_on_deletion = true' "VM deletion also deletes the OS disk"
require_text "${MODULE}/versions.tf" 'prevent_deletion_if_contains_resources = false' "group deletion removes remaining disks and IPs"
require_text "${MODULE}/versions.tf" 'backend "local" {}' "state backend is local"
require_text "${MODULE}/.terraform.lock.hcl" 'version     = "4.81.0"' "provider lock stays on AzureRM 4.81.0"
require_text "$SCRIPT" 'confirm DESTROY' "destroy requires the DESTROY confirmation"
require_text "$SCRIPT" 'confirm APPLY' "apply requires the APPLY confirmation"
require_text "$SCRIPT" 'AZURE_RUNNER_ALLOW_REPLACE' "apply refuses an unconfirmed replacement"
require_text "$SCRIPT" 'local state was restored' "a failed adopt restores local state"
require_text "$BICEP" "DenyAllInbound" "Bicep scaffold still declares the deny rule"

require_absent "${MODULE}/main.tf" 'access[[:space:]]*=[[:space:]]*"Allow"' "module opens no inbound allow rule"
require_absent "$SCRIPT" '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' \
  "wrapper contains no subscription id"
if grep -R -E -q 'BEGIN .+PRIVATE KEY' "$MODULE"; then
  not_ok "module contains no private key"
else
  ok "module contains no private key"
fi

if [[ "$failures" != "0" ]]; then
  printf '%s failed\n' "$failures" >&2
  exit 1
fi
printf 'azure runner stack checks passed\n'
