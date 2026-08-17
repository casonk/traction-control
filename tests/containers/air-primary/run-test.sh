#!/usr/bin/env bash

set -euo pipefail
umask 077

STAGED_ROOT="/opt/staged/util-repos"
PORTFOLIO_ROOT="/work/portfolio"
UTIL_REPOS_ROOT="${PORTFOLIO_ROOT}/util-repos"
TRACTION_REPO="${UTIL_REPOS_ROOT}/traction-control"
SNOWBRIDGE_REPO="${UTIL_REPOS_ROOT}/snowbridge"
PRIVATE_ROOT="/work/private"
CONFIG_PATH="${TRACTION_REPO}/config/air-primary.local.toml"
SAFE_INVENTORY="${PRIVATE_ROOT}/snowbridge-safe.json"
UNSAFE_INVENTORY="${PRIVATE_ROOT}/snowbridge-unsafe.json"
ENVIRONMENT_FILE="${PRIVATE_ROOT}/clockwork.env"
SHARE_PATH="${PRIVATE_ROOT}/share"
CERTS_DIR="${PRIVATE_ROOT}/certs"
AIR_ADDRESS="10.44.0.254"
MINI_ADDRESS="10.44.0.241"
PRO_ADDRESS="10.44.0.242"
WIREGUARD_INTERFACE="utun7"
CURRENT_ACCOUNT="$(id -un)"

fail() {
  printf 'not ok - %s\n' "$*" >&2
  exit 1
}

report_failure() {
  local status="$?"
  local diagnostic
  [[ "${status}" -ne 0 ]] || return 0
  printf 'Air-primary container regression failed with status %s.\n' "${status}" >&2
  for diagnostic in \
    "${PRIVATE_ROOT}/validate.stderr" \
    "${PRIVATE_ROOT}/render.stderr" \
    "${PRIVATE_ROOT}/repeat.stderr" \
    "${PRIVATE_ROOT}/unsafe.stderr" \
    "${PRIVATE_ROOT}/native-disabled-validate.stderr" \
    "${PRIVATE_ROOT}/native-disabled-render.stderr" \
    "${TRACTION_REPO}/artifacts/air-primary/generation-1/failure-report.json" \
    "${TRACTION_REPO}/artifacts/air-primary/generation-1/logs/clockwork.stderr.log" \
    "${TRACTION_REPO}/artifacts/air-primary/generation-1/logs/snowbridge.stderr.log" \
    "${TRACTION_REPO}/artifacts/air-primary/generation-1/logs/wiring-harness.stderr.log" \
    "${TRACTION_REPO}/artifacts/air-primary/generation-2/failure-report.json" \
    "${TRACTION_REPO}/artifacts/air-primary/generation-2/logs/snowbridge.stderr.log"; do
    if [[ -s "${diagnostic}" ]]; then
      printf '%s:\n' "${diagnostic}" >&2
      sed -n '1,160p' "${diagnostic}" >&2
    fi
  done
  return "${status}"
}
trap report_failure EXIT

expect_status() {
  local expected="$1"
  local actual="$2"
  local description="$3"
  [[ "${actual}" -eq "${expected}" ]] \
    || fail "${description}: expected status ${expected}, got ${actual}"
}

write_config() {
  local generation="$1"
  local inventory_path="$2"
  local native_smb_enabled="${3:-true}"
  cat >"${CONFIG_PATH}" <<EOF
schema_version = 1
mode = "render-only"
generation = ${generation}
deployment_id = "air-primary-container-test"

[network]
wireguard_interface = "${WIREGUARD_INTERFACE}"
air_address = "${AIR_ADDRESS}/32"
mini_address = "${MINI_ADDRESS}/32"
pro_address = "${PRO_ADDRESS}/32"

[repositories]
clockwork = "${UTIL_REPOS_ROOT}/clockwork"
snowbridge = "${UTIL_REPOS_ROOT}/snowbridge"
wiring_harness = "${UTIL_REPOS_ROOT}/wiring-harness"

[runtime]
python = "/usr/local/bin/python3"
caddy_binary = "/usr/bin/caddy"

[clockwork]
python = "/usr/local/bin/python3"
environment_file = "${ENVIRONMENT_FILE}"
backend_host = "127.0.0.1"
backend_port = 5001
edge_port = 8443

[snowbridge]
share_name = "snowbridge"
share_path = "${SHARE_PATH}"
expected_account = "${CURRENT_ACCOUNT}"
inventory_file = "${inventory_path}"
native_smb_enabled = ${native_smb_enabled}
web_backend_8080_enabled = true
web_backend_port = 8080
web_edge_port = 8444

[wiring_harness]
certs_dir = "${CERTS_DIR}"
EOF
  chmod 0600 "${CONFIG_PATH}"
}

install -d -m 0700 "${PORTFOLIO_ROOT}" "${PRIVATE_ROOT}" "/work/home"
cp -a "${STAGED_ROOT}" "${PORTFOLIO_ROOT}/"
install -d -m 0700 "${SHARE_PATH}" "${CERTS_DIR}" "/work/home/.config" "/work/home/.local"

export HOME="/work/home"
export XDG_CONFIG_HOME="/work/home/.config"
export XDG_DATA_HOME="/work/home/.local"
export PYTHONDONTWRITEBYTECODE=1
export GIT_OPTIONAL_LOCKS=0

# This is an isolated container-network fixture. It does not touch the host's
# WireGuard interface, routes, forwarding, PF rules, or Podman machine network.
ip link add "${WIREGUARD_INTERFACE}" type dummy
ip address add "${AIR_ADDRESS}/32" dev "${WIREGUARD_INTERFACE}"
ip link set "${WIREGUARD_INTERFACE}" up

TEST_SECRET="$(openssl rand -hex 32)"
export AIR_PRIMARY_TEST_SECRET="${TEST_SECRET}"
printf 'CLOCKWORK_WEB_SECRET=%s\n' "${TEST_SECRET}" >"${ENVIRONMENT_FILE}"
chmod 0600 "${ENVIRONMENT_FILE}"

cat >"${SAFE_INVENTORY}" <<EOF
{
  "schema_version": 1,
  "platform": "macos",
  "guest_smb_share_count": 0,
  "non_target_smb_share_count": 0,
  "target_share_state": "absent",
  "tcp_445_listeners": [],
  "pf_boundary_verified": false,
  "wireguard_interface_present": true,
  "wireguard_addresses": ["${AIR_ADDRESS}/32"]
}
EOF
cat >"${UNSAFE_INVENTORY}" <<EOF
{
  "schema_version": 1,
  "platform": "macos",
  "guest_smb_share_count": 0,
  "non_target_smb_share_count": 0,
  "target_share_state": "absent",
  "tcp_445_listeners": ["0.0.0.0"],
  "pf_boundary_verified": false,
  "wireguard_interface_present": true,
  "wireguard_addresses": ["${AIR_ADDRESS}/32"]
}
EOF
chmod 0600 "${SAFE_INVENTORY}" "${UNSAFE_INVENTORY}"

openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj '/CN=Air Primary container test CA' \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -keyout "${CERTS_DIR}/ca.key" \
  -out "${CERTS_DIR}/ca.crt" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes \
  -subj "/CN=${AIR_ADDRESS}" \
  -keyout "${CERTS_DIR}/server.key" \
  -out "${PRIVATE_ROOT}/server.csr" >/dev/null 2>&1
printf 'subjectAltName=IP:%s\nextendedKeyUsage=serverAuth\n' "${AIR_ADDRESS}" \
  >"${PRIVATE_ROOT}/server.ext"
openssl x509 -req -days 1 \
  -in "${PRIVATE_ROOT}/server.csr" \
  -CA "${CERTS_DIR}/ca.crt" \
  -CAkey "${CERTS_DIR}/ca.key" \
  -CAcreateserial \
  -extfile "${PRIVATE_ROOT}/server.ext" \
  -out "${CERTS_DIR}/server.crt" >/dev/null 2>&1
chmod 0600 "${CERTS_DIR}"/*

write_config 1 "${SAFE_INVENTORY}"

python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" validate \
  >"${PRIVATE_ROOT}/validate.stdout" \
  2>"${PRIVATE_ROOT}/validate.stderr"
[[ ! -s "${PRIVATE_ROOT}/validate.stderr" ]] \
  || fail "coordinator validation wrote unexpected stderr"

python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" render \
  >"${PRIVATE_ROOT}/render.stdout" \
  2>"${PRIVATE_ROOT}/render.stderr"
[[ ! -s "${PRIVATE_ROOT}/render.stderr" ]] \
  || fail "coordinator render wrote unexpected stderr"

/usr/bin/caddy validate \
  --config "${TRACTION_REPO}/artifacts/air-primary/generation-1/outputs/wiring/Caddyfile" \
  --adapter caddyfile \
  >"${PRIVATE_ROOT}/caddy-revalidate.stdout" \
  2>"${PRIVATE_ROOT}/caddy-revalidate.stderr"

/usr/local/bin/assert-air-primary-bundle positive \
  --util-repos-root "${UTIL_REPOS_ROOT}" \
  --private-root "${PRIVATE_ROOT}"
printf 'ok 1 - real child renderers and Caddy produced the inert Air-primary bundle\n'

set +e
python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" render \
  >"${PRIVATE_ROOT}/repeat.stdout" \
  2>"${PRIVATE_ROOT}/repeat.stderr"
repeat_status=$?
set -e
expect_status 2 "${repeat_status}" "immutable successful-generation rerender"
grep -F '"category": "unsafe_configuration"' "${PRIVATE_ROOT}/repeat.stderr" >/dev/null \
  || fail "rerender did not report unsafe_configuration"
grep -F 'generation already exists; refusing overwrite' "${PRIVATE_ROOT}/repeat.stderr" >/dev/null \
  || fail "rerender did not report immutable generation refusal"
/usr/local/bin/assert-air-primary-bundle unchanged \
  --util-repos-root "${UTIL_REPOS_ROOT}" \
  --private-root "${PRIVATE_ROOT}"
printf 'ok 2 - a successful generation is immutable\n'

write_config 2 "${UNSAFE_INVENTORY}"
set +e
python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" render \
  >"${PRIVATE_ROOT}/unsafe.stdout" \
  2>"${PRIVATE_ROOT}/unsafe.stderr"
unsafe_status=$?
set -e
expect_status 2 "${unsafe_status}" "unsafe Snowbridge inventory"
/usr/local/bin/assert-air-primary-bundle failure \
  --util-repos-root "${UTIL_REPOS_ROOT}" \
  --private-root "${PRIVATE_ROOT}"
cp -p \
  "${TRACTION_REPO}/artifacts/air-primary/generation-2/failure-report.json" \
  "${PRIVATE_ROOT}/failure-report.before-rerender.json"
printf 'ok 3 - unsafe wildcard SMB inventory fails closed before wiring render\n'

set +e
python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" render \
  >"${PRIVATE_ROOT}/failed-repeat.stdout" \
  2>"${PRIVATE_ROOT}/failed-repeat.stderr"
failed_repeat_status=$?
set -e
expect_status 2 "${failed_repeat_status}" "consumed failed-generation rerender"
grep -F 'generation already exists; refusing overwrite' \
  "${PRIVATE_ROOT}/failed-repeat.stderr" >/dev/null \
  || fail "failed generation was not treated as consumed"
cmp -s \
  "${PRIVATE_ROOT}/failure-report.before-rerender.json" \
  "${TRACTION_REPO}/artifacts/air-primary/generation-2/failure-report.json" \
  || fail "failed-generation rerender changed its failure evidence"
printf 'ok 4 - a failed generation remains consumed and immutable\n'

write_config 3 "${UNSAFE_INVENTORY}" false
python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" validate \
  >"${PRIVATE_ROOT}/native-disabled-validate.stdout" \
  2>"${PRIVATE_ROOT}/native-disabled-validate.stderr"
[[ ! -s "${PRIVATE_ROOT}/native-disabled-validate.stderr" ]] \
  || fail "native-SMB-disabled validation wrote unexpected stderr"

python3 "${TRACTION_REPO}/scripts/render_air_primary.py" \
  --config "${CONFIG_PATH}" render \
  >"${PRIVATE_ROOT}/native-disabled-render.stdout" \
  2>"${PRIVATE_ROOT}/native-disabled-render.stderr"
[[ ! -s "${PRIVATE_ROOT}/native-disabled-render.stderr" ]] \
  || fail "native-SMB-disabled render wrote unexpected stderr"

/usr/bin/caddy validate \
  --config "${TRACTION_REPO}/artifacts/air-primary/generation-3/outputs/wiring/Caddyfile" \
  --adapter caddyfile \
  >"${PRIVATE_ROOT}/native-disabled-caddy.stdout" \
  2>"${PRIVATE_ROOT}/native-disabled-caddy.stderr"

/usr/local/bin/assert-air-primary-bundle native-disabled \
  --util-repos-root "${UTIL_REPOS_ROOT}" \
  --private-root "${PRIVATE_ROOT}"
printf 'ok 5 - native SMB can be omitted while the independent web edge remains rendered\n'

printf '1..5\n'
