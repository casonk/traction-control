#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/rotate_azure_runner_secret.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/runner-secret-rotation-test.XXXXXX")"
chmod 700 "$TEST_ROOT"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

failures=0

check_status() {
  local expected="$1"
  local label="$2"
  shift 2
  local output
  local status
  if output="$("$@" 2>&1)"; then
    status=0
  else
    status=$?
  fi
  if [[ "$status" == "$expected" ]]; then
    printf 'ok - %s\n' "$label"
  else
    printf 'not ok - %s (expected %s, got %s)\n%s\n' \
      "$label" "$expected" "$status" "$output"
    failures=$((failures + 1))
  fi
}

check_contains() {
  local text="$1"
  local expected="$2"
  local label="$3"
  if [[ "$text" == *"$expected"* ]]; then
    printf 'ok - %s\n' "$label"
  else
    printf 'not ok - %s (missing %s)\n' "$label" "$expected"
    failures=$((failures + 1))
  fi
}

check_status 0 "help is available without Azure credentials" "$SCRIPT" --help
check_status 1 "missing token path is rejected" "$SCRIPT"
check_status 1 "extra arguments are rejected" "$SCRIPT" first second
check_status 1 "missing token file is rejected" "$SCRIPT" "${TEST_ROOT}/missing"

token_dir="${TEST_ROOT}/token"
mkdir -m 700 "$token_dir"
token_file="${token_dir}/github-token"
printf '%s' 'synthetic-placeholder-not-a-credential' > "$token_file"
chmod 600 "$token_file"

chmod 644 "$token_file"
output="$("$SCRIPT" "$token_file" 2>&1 || true)"
check_contains "$output" "must not grant group or other access" \
  "group/world-readable token file is rejected"
chmod 600 "$token_file"

printf '%s\n' 'synthetic-placeholder-not-a-credential' > "$token_file"
output="$("$SCRIPT" "$token_file" 2>&1 || true)"
check_contains "$output" "without whitespace" \
  "newline-terminated token file is rejected"
printf '%s' 'synthetic-placeholder-not-a-credential' > "$token_file"
chmod 600 "$token_file"

ln -s "$token_file" "${token_dir}/token-link"
output="$("$SCRIPT" "${token_dir}/token-link" 2>&1 || true)"
check_contains "$output" "non-symlink" "symlink token path is rejected"

chmod 755 "$token_dir"
output="$("$SCRIPT" "$token_file" 2>&1 || true)"
check_contains "$output" "parent directory must not grant group or other access" \
  "accessible token directory is rejected"
chmod 700 "$token_dir"

output="$(AZURE_CONFIG_DIR="${TEST_ROOT}/no-azure-cache" "$SCRIPT" "$token_file" 2>&1 || true)"
check_contains "$output" "Azure CLI config directory is missing" \
  "valid token cannot proceed without an Azure login cache"
if [[ "$output" == *"synthetic-placeholder-not-a-credential"* ]]; then
  printf 'not ok - token value is not printed during validation\n'
  failures=$((failures + 1))
else
  printf 'ok - token value is not printed during validation\n'
fi

fake_bin="${TEST_ROOT}/fake-bin"
fake_state="${TEST_ROOT}/fake-state"
azure_config="${TEST_ROOT}/azure-config"
mkdir -m 700 "$fake_bin" "$fake_state" "$azure_config"
printf '100\n' > "${fake_state}/deny-priority"
ssh-keygen -q -t ed25519 -N '' -C fake-host -f "${TEST_ROOT}/host-key"

cat > "${fake_bin}/podman" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "image" ]]; then
  exit 0
fi
[[ "${1:-}" == "run" ]] || exit 2
shift
while (($#)); do
  case "$1" in
    --rm)
      shift
      ;;
    --volume|--entrypoint)
      shift 2
      ;;
    *)
      shift
      break
      ;;
  esac
done
[[ "${1:-}" == "-c" ]] || exit 2
shift 2
[[ "${1:-}" == "azure-cli" ]] || exit 2
shift
exec "${TEST_AZ_STUB}" "$@"
STUB

cat > "${fake_bin}/az" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_AZ_ARGS_LOG"
case "${1:-} ${2:-} ${3:-} ${4:-}" in
  "account show"*)
    printf 'Enabled\n'
    ;;
  "vm get-instance-view"*)
    printf 'VM running\n'
    ;;
  "vm show"*)
    printf '20.80.1.138\n'
    ;;
  "network nsg rule list"*)
    query=''
    while (($#)); do
      if [[ "$1" == "--query" ]]; then
        query="$2"
        break
      fi
      shift
    done
    if [[ "$query" == *"runner-token-rotation-"* ]]; then
      if [[ -e "${TEST_FAKE_STATE}/allow-rule" ]]; then
        printf '1\n'
      else
        printf '0\n'
      fi
    elif [[ "$query" == *"length(@)"* ]]; then
      if [[ -e "${TEST_FAKE_STATE}/allow-rule" ]]; then
        printf '2\n'
      else
        printf '1\n'
      fi
    else
      printf '1\n'
    fi
    ;;
  "network nsg rule update"*)
    if [[ "$*" == *"--name DenyAllInbound"* ]]; then
      priority=''
      while (($#)); do
        if [[ "$1" == "--priority" ]]; then
          priority="$2"
          break
        fi
        shift
      done
      printf '%s\n' "$priority" > "${TEST_FAKE_STATE}/deny-priority"
    else
      printf 'deny\n' > "${TEST_FAKE_STATE}/temporary-rule-access"
    fi
    ;;
  "network nsg rule create"*)
    : > "${TEST_FAKE_STATE}/allow-rule"
    ;;
  "network nsg rule delete"*)
    rm -f "${TEST_FAKE_STATE}/allow-rule"
    ;;
  "vm run-command invoke"*)
    scripts=''
    while (($#)); do
      if [[ "$1" == "--scripts" ]]; then
        scripts="$2"
        break
      fi
      shift
    done
    printf '%s\n' "$scripts" | /bin/sh -n
    if [[ "$scripts" == *"RUNNER_SECRET_ENCRYPTION_OK"* ]]; then
      if [[ "${TEST_ENCRYPTION_OK:-1}" == "1" ]]; then
        printf '__RUNNER_SECRET_ENCRYPTION_OK=1\n'
      else
        printf 'encryption is not ready\n' >&2
        exit 1
      fi
    elif [[ "$scripts" == *"RUNNER_ROTATION_KEY_INSTALLED"* ]]; then
      : > "${TEST_FAKE_STATE}/guest-key"
      printf '__RUNNER_ROTATION_KEY_INSTALLED=1\n'
      cat "${TEST_HOST_PUBLIC_KEY}"
    elif [[ "$scripts" == *"RUNNER_ROTATION_KEY_REMOVED"* ]]; then
      rm -f "${TEST_FAKE_STATE}/guest-key"
      printf '__RUNNER_ROTATION_KEY_REMOVED=1\n'
    else
      exit 3
    fi
    ;;
  *)
    printf 'unexpected Azure CLI call: %s\n' "$*" >&2
    exit 2
    ;;
esac
STUB

cat > "${fake_bin}/curl" <<'STUB'
#!/usr/bin/env bash
printf '187.14.116.77\n'
STUB

cat > "${fake_bin}/ssh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" true " ]]; then
  for argument in "$@"; do
    [[ "$argument" == "StrictHostKeyChecking=yes" ]] && exit 0
  done
  printf 'SSH did not pin the VM host key\n' >&2
  exit 1
fi
cat > "$TEST_SSH_STDIN_CAPTURE"
if [[ "${TEST_SSH_ROTATION_FAIL:-0}" == "1" ]]; then
  exit 42
fi
printf 'Encrypted runner-auth Secret replaced and verified nonempty.\n'
STUB

chmod 700 "${fake_bin}/podman" "${fake_bin}/az" "${fake_bin}/curl" "${fake_bin}/ssh"
printf '%s' 'synthetic-placeholder-not-a-credential' > "$token_file"
chmod 600 "$token_file"
if output="$(printf 'ROTATE\n' | env \
  PATH="${fake_bin}:${PATH}" \
  AZURE_CONFIG_DIR="$azure_config" \
  TEST_AZ_STUB="${fake_bin}/az" \
  TEST_AZ_ARGS_LOG="${TEST_ROOT}/azure-args.log" \
  TEST_FAKE_STATE="$fake_state" \
  TEST_HOST_PUBLIC_KEY="${TEST_ROOT}/host-key.pub" \
  TEST_SSH_STDIN_CAPTURE="${TEST_ROOT}/ssh-stdin" \
  TEST_ENCRYPTION_OK=0 \
  "$SCRIPT" "$token_file" 2>&1)"; then
  printf 'not ok - rotation stops when Kubernetes encryption is unverified\n'
  failures=$((failures + 1))
else
  check_contains "$output" "encryption is not ready" \
    "rotation stops when Kubernetes encryption is unverified"
fi
if [[ ! -e "${fake_state}/allow-rule" ]] \
  && [[ ! -e "${fake_state}/guest-key" ]] \
  && [[ "$(cat "${fake_state}/deny-priority")" == "100" ]]; then
  printf 'ok - encryption failure opens no temporary SSH access\n'
else
  printf 'not ok - encryption failure opens no temporary SSH access\n'
  failures=$((failures + 1))
fi

if output="$(printf 'ROTATE\n' | env \
  PATH="${fake_bin}:${PATH}" \
  AZURE_CONFIG_DIR="$azure_config" \
  TEST_AZ_STUB="${fake_bin}/az" \
  TEST_AZ_ARGS_LOG="${TEST_ROOT}/azure-args.log" \
  TEST_FAKE_STATE="$fake_state" \
  TEST_HOST_PUBLIC_KEY="${TEST_ROOT}/host-key.pub" \
  TEST_SSH_STDIN_CAPTURE="${TEST_ROOT}/ssh-stdin" \
  TEST_SSH_ROTATION_FAIL=1 \
  "$SCRIPT" "$token_file" 2>&1)"; then
  printf 'not ok - failed Secret update returns failure\n'
  failures=$((failures + 1))
else
  printf 'ok - failed Secret update returns failure\n'
fi
if [[ ! -e "${fake_state}/allow-rule" ]] \
  && [[ ! -e "${fake_state}/guest-key" ]] \
  && [[ "$(cat "${fake_state}/deny-priority")" == "100" ]]; then
  printf 'ok - failed Secret update also cleans temporary SSH access\n'
else
  printf 'not ok - failed Secret update also cleans temporary SSH access\n'
  failures=$((failures + 1))
fi

if output="$(printf 'ROTATE\n' | env \
  PATH="${fake_bin}:${PATH}" \
  AZURE_CONFIG_DIR="$azure_config" \
  TEST_AZ_STUB="${fake_bin}/az" \
  TEST_AZ_ARGS_LOG="${TEST_ROOT}/azure-args.log" \
  TEST_FAKE_STATE="$fake_state" \
  TEST_HOST_PUBLIC_KEY="${TEST_ROOT}/host-key.pub" \
  TEST_SSH_STDIN_CAPTURE="${TEST_ROOT}/ssh-stdin" \
  "$SCRIPT" "$token_file" 2>&1)"; then
  printf 'ok - rotation completes with a verified SSH host key\n'
else
  printf 'not ok - rotation completes with a verified SSH host key\n%s\n' "$output"
  failures=$((failures + 1))
fi
if [[ -f "${TEST_ROOT}/ssh-stdin" ]] \
  && cmp -s "$token_file" "${TEST_ROOT}/ssh-stdin"; then
  printf 'ok - token is streamed through SSH stdin\n'
else
  printf 'not ok - token is streamed through SSH stdin\n'
  failures=$((failures + 1))
fi
if [[ -f "${TEST_ROOT}/azure-args.log" ]] \
  && ! grep -Fq 'synthetic-placeholder-not-a-credential' "${TEST_ROOT}/azure-args.log"; then
  printf 'ok - token is never passed to Azure CLI arguments\n'
else
  printf 'not ok - token is never passed to Azure CLI arguments\n'
  failures=$((failures + 1))
fi
if [[ ! -e "${fake_state}/allow-rule" ]] \
  && [[ ! -e "${fake_state}/guest-key" ]] \
  && [[ "$(cat "${fake_state}/deny-priority")" == "100" ]]; then
  printf 'ok - temporary SSH access is removed and deny-all is restored\n'
else
  printf 'not ok - temporary SSH access is removed and deny-all is restored\n'
  failures=$((failures + 1))
fi

if (( failures != 0 )); then
  printf '%s credential rotation test(s) failed\n' "$failures" >&2
  exit 1
fi
printf 'all runner secret rotation validation tests passed\n'
