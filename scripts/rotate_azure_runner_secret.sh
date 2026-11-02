#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="rg-arc-pilot"
VM_NAME="arcpilot-vm"
VM_USER="runneradmin"
NSG_NAME="arcpilot-nsg"
DENY_RULE_NAME="DenyAllInbound"
AZURE_CLI_IMAGE="mcr.microsoft.com/azure-cli:2.91.0"
AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-${HOME}/.azure}"

usage() {
  cat <<'EOF'
Usage: rotate_azure_runner_secret.sh OWNER_ONLY_TOKEN_FILE

Replace the encrypted ARC runner-auth Kubernetes Secret on the Azure pilot.
The file must contain one fine-grained GitHub PAT for the pilot repository
with Administration: read/write, without whitespace or a trailing newline.
The script temporarily allows SSH from this machine's current public IPv4,
pins the VM SSH host key obtained through Azure Run Command, streams the PAT
over SSH stdin, and removes temporary access on exit.
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
[[ "$#" == "1" ]] || fail "provide exactly one token-file path; use --help"

TOKEN_FILE="$1"
for command_name in podman stat find grep curl python3 ssh ssh-keygen base64 awk mktemp; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command not found: ${command_name}"
done

[[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" ]] \
  || fail "token path must be a regular, non-symlink file"
python3 - "$TOKEN_FILE" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
try:
    parent_stat = os.stat(os.path.dirname(os.path.abspath(path)), follow_symlinks=False)
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError("token parent is not a directory")
    if parent_stat.st_uid != os.getuid():
        raise ValueError("token file and parent directory must be owned by the current user")
    if parent_stat.st_mode & 0o077:
        raise ValueError("token parent directory must not grant group or other access")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    file_stat = os.fstat(descriptor)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("token path is not a regular file")
    if file_stat.st_uid != os.getuid():
        raise ValueError("token file must be owned by the current user")
    if file_stat.st_mode & 0o077:
        raise ValueError("token file must not grant group or other access")
    if file_stat.st_size < 1 or file_stat.st_size > 4096:
        raise ValueError("token file must contain between 1 and 4096 bytes")
    with os.fdopen(descriptor, "rb") as token_file:
        value = token_file.read(4097)
    if len(value) != file_stat.st_size or any(byte < 33 or byte > 126 for byte in value):
        raise ValueError("token file must contain one nonempty printable ASCII value without whitespace")
except (OSError, ValueError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY

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
[[ -z "$unsafe_path" ]] \
  || fail "Azure CLI files and directories must not grant group or other permissions: ${unsafe_path}"
podman image exists "$AZURE_CLI_IMAGE" \
  || fail "Azure CLI image is not available locally: ${AZURE_CLI_IMAGE}"

azure_cli() {
  podman run --rm \
    --volume "${AZURE_CONFIG_DIR}:/source:ro,z" \
    --entrypoint /bin/sh \
    "$AZURE_CLI_IMAGE" \
    -c '
      set -eu
      state_dir="$(mktemp -d /tmp/azure-state.XXXXXX)"
      trap '\''rm -rf "$state_dir"'\'' EXIT
      cp -a /source/. "$state_dir/"
      export AZURE_CONFIG_DIR="$state_dir"
      az "$@"
    ' azure-cli "$@"
}

subscription_state="$(azure_cli account show --only-show-errors --query state --output tsv)"
[[ "$subscription_state" == "Enabled" ]] || fail "Azure subscription is not enabled"
vm_state="$(azure_cli vm get-instance-view \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --only-show-errors \
  --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus | [0]" \
  --output tsv)"
[[ "$vm_state" == "VM running" ]] \
  || fail "Azure VM is not running (state: ${vm_state})"

VM_PUBLIC_IP="$(azure_cli vm show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --show-details \
  --only-show-errors \
  --query publicIps \
  --output tsv)"
python3 - "$VM_PUBLIC_IP" <<'PY'
import ipaddress
import sys
try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit("error: Azure VM does not have a valid public IP") from None
if address.version != 4 or not address.is_global:
    raise SystemExit("error: Azure VM public IP must be a globally routable IPv4 address")
PY

OPERATOR_IP="$(curl --ipv4 --fail --silent --show-error --max-time 15 https://api.ipify.org)"
python3 - "$OPERATOR_IP" <<'PY'
import ipaddress
import sys
try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit("error: could not determine the operator's public IPv4 address") from None
if address.version != 4 or not address.is_global:
    raise SystemExit("error: operator address is not a globally routable IPv4 address")
PY
OPERATOR_CIDR="${OPERATOR_IP}/32"

rule_count="$(azure_cli network nsg rule list \
  --resource-group "$RESOURCE_GROUP" \
  --nsg-name "$NSG_NAME" \
  --only-show-errors \
  --query 'length(@)' \
  --output tsv)"
[[ "$rule_count" == "1" ]] \
  || fail "expected exactly one custom NSG rule; refusing to alter an unfamiliar ingress policy"
deny_rule_count="$(azure_cli network nsg rule list \
  --resource-group "$RESOURCE_GROUP" \
  --nsg-name "$NSG_NAME" \
  --only-show-errors \
  --query "[?name == '${DENY_RULE_NAME}' && priority == \`100\` && direction == 'Inbound' && access == 'Deny' && protocol == '*' && sourceAddressPrefix == '*' && destinationAddressPrefix == '*' && destinationPortRange == '*'] | length(@)" \
  --output tsv)"
[[ "$deny_rule_count" == "1" ]] \
  || fail "the expected deny-all-inbound rule is missing or has drifted"

encryption_check_script="$(cat <<'REMOTE_SCRIPT'
set -eu
status="$(/usr/local/bin/k3s secrets-encrypt status)"
printf '%s\n' "$status"
printf '%s\n' "$status" | grep -q '^Encryption Status: Enabled$'
printf '%s\n' "$status" | grep -q '^Current Rotation Stage: reencrypt_finished$'
printf '%s\n' "$status" | grep -q '^Server Encryption Hashes: All hashes match$'
printf '__RUNNER_SECRET_ENCRYPTION_OK=1\n'
REMOTE_SCRIPT
)"
encryption_result="$(azure_cli vm run-command invoke \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --command-id RunShellScript \
  --scripts "$encryption_check_script" \
  --only-show-errors \
  --query 'value[].message' \
  --output tsv)"
grep -Fq '__RUNNER_SECRET_ENCRYPTION_OK=1' <<< "$encryption_result" \
  || fail "k3s secret encryption is not enabled and fully re-encrypted"

printf 'This rotates the encrypted runner-auth Secret for %s/%s.\n' "$RESOURCE_GROUP" "$VM_NAME"
printf 'The token value will not be printed or sent through Azure Run Command.\n'
printf 'Temporary SSH will be limited to %s; the VM remains billable.\n' "$OPERATOR_CIDR"
printf 'Only proceed after creating a repo-scoped fine-grained PAT with Administration read/write.\n'
printf 'Type ROTATE to continue: '
read -r confirmation
[[ "$confirmation" == "ROTATE" ]] || fail "operation cancelled"

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/arc-token-rotation.XXXXXX")"
chmod 700 "$temporary_dir"
python3 - "$TOKEN_FILE" "${temporary_dir}/github-token" <<'PY'
import os
import stat
import sys

source, destination = sys.argv[1:]
try:
    parent_stat = os.stat(os.path.dirname(os.path.abspath(source)), follow_symlinks=False)
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o077:
        raise ValueError("token parent directory must be current-user-owned and owner-only")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    source_fd = os.open(source, flags)
    source_stat = os.fstat(source_fd)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_uid != os.getuid() or source_stat.st_mode & 0o077:
        raise ValueError("token source must be a current-user-owned, owner-only regular file")
    if source_stat.st_size < 1 or source_stat.st_size > 4096:
        raise ValueError("token file must contain between 1 and 4096 bytes")
    with os.fdopen(source_fd, "rb") as handle:
        value = handle.read(4097)
    if len(value) != source_stat.st_size or any(byte < 33 or byte > 126 for byte in value):
        raise ValueError("token file must contain one nonempty printable ASCII value without whitespace")
    destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(destination_fd, "wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
except (OSError, ValueError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
TOKEN_FILE="${temporary_dir}/github-token"
ROTATION_SUFFIX="${$}-${RANDOM}"
SSH_RULE_NAME="runner-token-rotation-${ROTATION_SUFFIX}"
DENY_PRIORITY_MAY_HAVE_CHANGED=0
SSH_RULE_MAY_EXIST=0
GUEST_KEY_MAY_EXIST=0
trap 'rm -rf -- "$temporary_dir"' EXIT

cleanup() {
  result=$?
  trap - EXIT
  cleanup_failed=0

  if [[ "$GUEST_KEY_MAY_EXIST" == "1" ]]; then
    remove_key_script="$(cat <<REMOTE_SCRIPT
set -eu
authorized_keys=/home/${VM_USER}/.ssh/authorized_keys
temporary_file="\$(mktemp /home/${VM_USER}/.ssh/authorized_keys.XXXXXX)"
key="\$(printf '%s' '${TEMP_PUBLIC_KEY_B64}' | base64 -d)"
awk -v key="\$key" '\$0 != key' "\$authorized_keys" > "\$temporary_file"
chown ${VM_USER}:${VM_USER} "\$temporary_file"
chmod 600 "\$temporary_file"
mv "\$temporary_file" "\$authorized_keys"
printf '__RUNNER_ROTATION_KEY_REMOVED=1\\n'
REMOTE_SCRIPT
)"
    remove_result="$(azure_cli vm run-command invoke \
      --resource-group "$RESOURCE_GROUP" \
      --name "$VM_NAME" \
      --command-id RunShellScript \
      --scripts "$remove_key_script" \
      --only-show-errors \
      --query 'value[].message' \
      --output tsv 2>&1)" || {
        printf 'cleanup error: could not remove temporary guest SSH key\n%s\n' "$remove_result" >&2
        cleanup_failed=1
      }
    if [[ -n "${remove_result:-}" ]] && ! grep -Fq '__RUNNER_ROTATION_KEY_REMOVED=1' <<< "$remove_result"; then
      printf 'cleanup error: guest SSH-key removal was not confirmed\n%s\n' "$remove_result" >&2
      cleanup_failed=1
    fi
  fi

  ssh_rule_removed=1
  if [[ "$SSH_RULE_MAY_EXIST" == "1" ]]; then
    matching_rules="$(azure_cli network nsg rule list \
      --resource-group "$RESOURCE_GROUP" \
      --nsg-name "$NSG_NAME" \
      --only-show-errors \
      --query "[?name == '${SSH_RULE_NAME}'] | length(@)" \
      --output tsv)" || {
      printf 'cleanup error: could not determine whether temporary SSH NSG rule %s remains\n' \
        "$SSH_RULE_NAME" >&2
      cleanup_failed=1
      ssh_rule_removed=0
      matching_rules=unknown
    }
    if [[ "$matching_rules" == "1" ]]; then
      azure_cli network nsg rule update \
        --resource-group "$RESOURCE_GROUP" \
        --nsg-name "$NSG_NAME" \
        --name "$SSH_RULE_NAME" \
        --access Deny \
        --only-show-errors >/dev/null 2>&1 || true
      if ! azure_cli network nsg rule delete \
        --resource-group "$RESOURCE_GROUP" \
        --nsg-name "$NSG_NAME" \
        --name "$SSH_RULE_NAME" \
        --only-show-errors; then
        printf 'cleanup error: temporary SSH NSG rule %s could not be deleted\n' "$SSH_RULE_NAME" >&2
        printf 'The rule was changed to Deny where possible; inspect it in Azure immediately.\n' >&2
        cleanup_failed=1
        ssh_rule_removed=0
      fi
    elif [[ "$matching_rules" != "0" ]]; then
      printf 'cleanup error: unexpected count for temporary SSH NSG rule %s: %s\n' \
        "$SSH_RULE_NAME" "$matching_rules" >&2
      cleanup_failed=1
      ssh_rule_removed=0
    fi
  fi

  if [[ "$DENY_PRIORITY_MAY_HAVE_CHANGED" == "1" && "$ssh_rule_removed" == "1" ]]; then
    if ! azure_cli network nsg rule update \
      --resource-group "$RESOURCE_GROUP" \
      --nsg-name "$NSG_NAME" \
      --name "$DENY_RULE_NAME" \
      --priority 100 \
      --only-show-errors; then
      printf 'cleanup error: could not restore %s priority to 100\n' "$DENY_RULE_NAME" >&2
      cleanup_failed=1
    fi
  elif [[ "$DENY_PRIORITY_MAY_HAVE_CHANGED" == "1" && "$ssh_rule_removed" == "0" ]]; then
    printf 'warning: %s remains at priority 110 to avoid a priority collision; inspect NSG %s\n' \
      "$DENY_RULE_NAME" "$NSG_NAME" >&2
  fi

  rm -rf -- "$temporary_dir"
  if [[ "$cleanup_failed" == "1" ]]; then
    exit 1
  fi
  exit "$result"
}
trap cleanup EXIT

ssh-keygen -q -t ed25519 -N '' -C arc-runner-token-rotation \
  -f "${temporary_dir}/id_ed25519"
TEMP_PUBLIC_KEY="$(cat "${temporary_dir}/id_ed25519.pub")"
TEMP_PUBLIC_KEY_B64="$(printf '%s' "$TEMP_PUBLIC_KEY" | base64 -w0)"

DENY_PRIORITY_MAY_HAVE_CHANGED=1
azure_cli network nsg rule update \
  --resource-group "$RESOURCE_GROUP" \
  --nsg-name "$NSG_NAME" \
  --name "$DENY_RULE_NAME" \
  --priority 110 \
  --only-show-errors >/dev/null

SSH_RULE_MAY_EXIST=1
azure_cli network nsg rule create \
  --resource-group "$RESOURCE_GROUP" \
  --nsg-name "$NSG_NAME" \
  --name "$SSH_RULE_NAME" \
  --priority 100 \
  --direction Inbound \
  --access Allow \
  --protocol Tcp \
  --source-address-prefixes "$OPERATOR_CIDR" \
  --source-port-ranges '*' \
  --destination-address-prefixes '*' \
  --destination-port-ranges 22 \
  --only-show-errors >/dev/null

GUEST_KEY_MAY_EXIST=1
install_key_script="$(cat <<REMOTE_SCRIPT
set -eu
ssh_directory=/home/${VM_USER}/.ssh
install -d -o ${VM_USER} -g ${VM_USER} -m 700 "\$ssh_directory"
authorized_keys="\$ssh_directory/authorized_keys"
touch "\$authorized_keys"
chown ${VM_USER}:${VM_USER} "\$authorized_keys"
chmod 600 "\$authorized_keys"
key="\$(printf '%s' '${TEMP_PUBLIC_KEY_B64}' | base64 -d)"
printf '\\n%s\\n' "\$key" >> "\$authorized_keys"
chown ${VM_USER}:${VM_USER} "\$authorized_keys"
chmod 600 "\$authorized_keys"
printf '__RUNNER_ROTATION_KEY_INSTALLED=1\\n'
cat /etc/ssh/ssh_host_ed25519_key.pub
REMOTE_SCRIPT
)"
install_result="$(azure_cli vm run-command invoke \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --command-id RunShellScript \
  --scripts "$install_key_script" \
  --only-show-errors \
  --query 'value[].message' \
  --output tsv)"
grep -Fq '__RUNNER_ROTATION_KEY_INSTALLED=1' <<< "$install_result" \
  || fail "Azure did not confirm temporary SSH-key installation"
HOST_PUBLIC_KEY="$(awk '$1 == "ssh-ed25519" && NF >= 2 {print $1 " " $2; exit}' <<< "$install_result")"
[[ -n "$HOST_PUBLIC_KEY" ]] || fail "Azure did not return the VM's SSH host public key"
ssh-keygen -lf <(printf '%s\n' "$HOST_PUBLIC_KEY") >/dev/null \
  || fail "the VM SSH host public key is malformed"
printf '%s %s\n' "$VM_PUBLIC_IP" "$HOST_PUBLIC_KEY" > "${temporary_dir}/known_hosts"
chmod 600 "${temporary_dir}/known_hosts"

ssh_options=(
  -F /dev/null
  -i "${temporary_dir}/id_ed25519"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o PreferredAuthentications=publickey
  -o PasswordAuthentication=no
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${temporary_dir}/known_hosts"
  -o GlobalKnownHostsFile=/dev/null
  -o ControlMaster=no
  -o ClearAllForwardings=yes
  -o ConnectTimeout=10
  -o LogLevel=ERROR
)
connected=0
for attempt in {1..12}; do
  if ssh "${ssh_options[@]}" "${VM_USER}@${VM_PUBLIC_IP}" true; then
    connected=1
    break
  fi
  sleep 5
done
[[ "$connected" == "1" ]] || fail "could not establish SSH using the verified temporary key"

remote_rotation_command="$(cat <<'REMOTE_COMMAND'
sudo -n /bin/bash -o pipefail -c '
  kubectl=/usr/local/bin/kubectl
  "$kubectl" create secret generic runner-auth \
    --namespace arc-runners \
    --from-file=github_token=/dev/stdin \
    --dry-run=client \
    --output=yaml \
    | "$kubectl" apply --filename=-
  secret_data="$("$kubectl" get secret runner-auth \
    --namespace arc-runners \
    --output=jsonpath="{.data.github_token}")"
  test -n "$secret_data"
  printf "Encrypted runner-auth Secret replaced and verified nonempty.\\n"
'
REMOTE_COMMAND
)"
ssh "${ssh_options[@]}" "${VM_USER}@${VM_PUBLIC_IP}" "$remote_rotation_command" < "$TOKEN_FILE"
