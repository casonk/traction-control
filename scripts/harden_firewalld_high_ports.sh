#!/usr/bin/env bash
set -euo pipefail

MODE="check"
INTERFACE="enp5s0"
ZONE=""
WIREGUARD_PORT="51820"
STATE_FILE="/var/lib/traction-control/firewalld-high-port-hardening.env"
HIGH_PORTS=("1025-65535/tcp" "1025-65535/udp")

usage() {
  cat <<'EOF'
Usage: harden_firewalld_high_ports.sh [MODE] [OPTIONS]

Remove blanket high-port allowances from one firewalld interface zone while
preserving an explicit WireGuard UDP ingress rule.

Modes:
  --check      Report current exposure without changing it (default).
  --apply      Snapshot targeted state, apply the hardening, and verify it.
  --rollback   Restore the targeted rules recorded by the last --apply.

Options:
  --interface NAME       LAN/WAN-facing interface (default: enp5s0).
  --zone NAME            Override firewalld zone auto-detection.
  --wireguard-port PORT  UDP port to preserve (default: 51820).
  --state-file PATH      Root-owned rollback state file.
  --help                 Show this help.

Examples:
  bash scripts/harden_firewalld_high_ports.sh --check
  sudo bash scripts/harden_firewalld_high_ports.sh --apply
  sudo bash scripts/harden_firewalld_high_ports.sh --rollback
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '%s\n' "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_root() {
  (( EUID == 0 )) || fail "run this mode with sudo"
}

validate_port() {
  [[ "$1" =~ ^[0-9]+$ ]] || fail "invalid WireGuard port: $1"
  (( 10#$1 >= 1 && 10#$1 <= 65535 )) || fail "WireGuard port must be between 1 and 65535"
}

detect_zone() {
  local detected

  if [[ -n "${ZONE}" ]]; then
    printf '%s\n' "${ZONE}"
    return
  fi

  detected="$(firewall-cmd --get-zone-of-interface="${INTERFACE}" 2>/dev/null || true)"
  [[ -n "${detected}" ]] || fail "no active firewalld zone found for interface ${INTERFACE}"
  printf '%s\n' "${detected}"
}

query_runtime_port() {
  firewall-cmd --zone="$1" --query-port="$2" >/dev/null 2>&1
}

query_permanent_port() {
  firewall-cmd --permanent --zone="$1" --query-port="$2" >/dev/null 2>&1
}

list_has_exact_port() {
  local port_list="$1"
  local wanted="$2"
  local listed

  for listed in ${port_list}; do
    [[ "${listed}" == "${wanted}" ]] && return 0
  done
  return 1
}

has_exact_runtime_port() {
  list_has_exact_port "$(firewall-cmd --zone="$1" --list-ports)" "$2"
}

has_exact_permanent_port() {
  list_has_exact_port "$(firewall-cmd --permanent --zone="$1" --list-ports)" "$2"
}

bool_for_runtime_port() {
  if has_exact_runtime_port "$1" "$2"; then
    printf 'true\n'
  else
    printf 'false\n'
  fi
}

bool_for_permanent_port() {
  if has_exact_permanent_port "$1" "$2"; then
    printf 'true\n'
  else
    printf 'false\n'
  fi
}

report_state() {
  local zone="$1"
  local port

  log "interface: ${INTERFACE}"
  log "zone: ${zone}"
  log "runtime services: $(firewall-cmd --zone="${zone}" --list-services)"
  log "runtime ports: $(firewall-cmd --zone="${zone}" --list-ports)"

  for port in "${HIGH_PORTS[@]}" "${WIREGUARD_PORT}/udp"; do
    log "runtime ${port}: $(bool_for_runtime_port "${zone}" "${port}")"
  done

  if (( EUID == 0 )); then
    log "permanent services: $(firewall-cmd --permanent --zone="${zone}" --list-services)"
    log "permanent ports: $(firewall-cmd --permanent --zone="${zone}" --list-ports)"
    for port in "${HIGH_PORTS[@]}" "${WIREGUARD_PORT}/udp"; do
      log "permanent ${port}: $(bool_for_permanent_port "${zone}" "${port}")"
    done
  else
    log "permanent state: unavailable without elevated firewalld authorization"
  fi
}

write_state() {
  local zone="$1"
  local state_dir
  local temp_file

  if [[ -f "${STATE_FILE}" ]]; then
    log "preserving existing rollback state: ${STATE_FILE}"
    return
  fi

  state_dir="$(dirname "${STATE_FILE}")"
  install -d -m 0700 "${state_dir}"
  temp_file="$(mktemp "${state_dir}/firewalld-high-port-hardening.XXXXXX")"

  {
    printf 'STATE_VERSION=1\n'
    printf 'INTERFACE=%q\n' "${INTERFACE}"
    printf 'ZONE=%q\n' "${zone}"
    printf 'WIREGUARD_PORT=%q\n' "${WIREGUARD_PORT}"
    printf 'RUNTIME_TCP_HIGH=%q\n' "$(bool_for_runtime_port "${zone}" "1025-65535/tcp")"
    printf 'RUNTIME_UDP_HIGH=%q\n' "$(bool_for_runtime_port "${zone}" "1025-65535/udp")"
    printf 'RUNTIME_WIREGUARD=%q\n' "$(bool_for_runtime_port "${zone}" "${WIREGUARD_PORT}/udp")"
    printf 'PERMANENT_TCP_HIGH=%q\n' "$(bool_for_permanent_port "${zone}" "1025-65535/tcp")"
    printf 'PERMANENT_UDP_HIGH=%q\n' "$(bool_for_permanent_port "${zone}" "1025-65535/udp")"
    printf 'PERMANENT_WIREGUARD=%q\n' "$(bool_for_permanent_port "${zone}" "${WIREGUARD_PORT}/udp")"
  } > "${temp_file}"

  chmod 0600 "${temp_file}"
  mv -f "${temp_file}" "${STATE_FILE}"
  log "saved rollback state: ${STATE_FILE}"
}

verify_hardened() {
  local zone="$1"
  local failed=0

  for port in "${HIGH_PORTS[@]}"; do
    if has_exact_runtime_port "${zone}" "${port}" ||
       has_exact_permanent_port "${zone}" "${port}"; then
      printf 'verification failed: broad port range remains allowed: %s\n' "${port}" >&2
      failed=1
    fi
  done

  if ! has_exact_runtime_port "${zone}" "${WIREGUARD_PORT}/udp" ||
     ! has_exact_permanent_port "${zone}" "${WIREGUARD_PORT}/udp"; then
    printf 'verification failed: WireGuard port is not allowed: %s/udp\n' "${WIREGUARD_PORT}" >&2
    failed=1
  fi

  (( failed == 0 )) || return 1
  log "verification passed: broad high-port ranges are closed and WireGuard ${WIREGUARD_PORT}/udp is preserved"
}

apply_hardening() {
  local zone="$1"
  local port

  require_root
  write_state "${zone}"

  if ! has_exact_permanent_port "${zone}" "${WIREGUARD_PORT}/udp"; then
    firewall-cmd --permanent --zone="${zone}" --add-port="${WIREGUARD_PORT}/udp" >/dev/null
    log "added permanent WireGuard rule: ${WIREGUARD_PORT}/udp"
  fi

  for port in "${HIGH_PORTS[@]}"; do
    if has_exact_permanent_port "${zone}" "${port}"; then
      firewall-cmd --permanent --zone="${zone}" --remove-port="${port}" >/dev/null
      log "removed permanent broad allowance: ${port}"
    fi
  done

  firewall-cmd --reload >/dev/null
  verify_hardened "${zone}"
  report_state "${zone}"
}

load_state() {
  local owner
  local mode

  [[ -f "${STATE_FILE}" ]] || fail "rollback state does not exist: ${STATE_FILE}"
  owner="$(stat -c '%u' "${STATE_FILE}")"
  mode="$(stat -c '%a' "${STATE_FILE}")"
  [[ "${owner}" == "0" ]] || fail "rollback state must be owned by root: ${STATE_FILE}"
  (( (8#${mode} & 8#022) == 0 )) ||
    fail "rollback state must not be group- or world-writable: ${STATE_FILE}"
  # The file is generated by this script under a root-only directory.
  # shellcheck source=/dev/null
  source "${STATE_FILE}"
  [[ "${STATE_VERSION:-}" == "1" ]] || fail "unsupported rollback state version"
  [[ "${INTERFACE:-}" =~ ^[A-Za-z0-9_.:-]+$ ]] || fail "invalid interface in rollback state"
  [[ "${ZONE:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "invalid zone in rollback state"
  validate_port "${WIREGUARD_PORT:-}"
  for saved_bool in \
    "${RUNTIME_TCP_HIGH:-}" \
    "${RUNTIME_UDP_HIGH:-}" \
    "${RUNTIME_WIREGUARD:-}" \
    "${PERMANENT_TCP_HIGH:-}" \
    "${PERMANENT_UDP_HIGH:-}" \
    "${PERMANENT_WIREGUARD:-}"; do
    [[ "${saved_bool}" == "true" || "${saved_bool}" == "false" ]] ||
      fail "invalid boolean in rollback state"
  done
}

restore_if_true() {
  local zone="$1"
  local port="$2"
  local wanted="$3"

  if [[ "${wanted}" == "true" ]] && ! has_exact_permanent_port "${zone}" "${port}"; then
    firewall-cmd --permanent --zone="${zone}" --add-port="${port}" >/dev/null
    log "restored permanent allowance: ${port}"
  elif [[ "${wanted}" == "false" ]] && has_exact_permanent_port "${zone}" "${port}"; then
    firewall-cmd --permanent --zone="${zone}" --remove-port="${port}" >/dev/null
    log "removed rule absent from the saved state: ${port}"
  fi
}

restore_runtime_if_true() {
  local zone="$1"
  local port="$2"
  local wanted="$3"

  if [[ "${wanted}" == "true" ]] && ! has_exact_runtime_port "${zone}" "${port}"; then
    firewall-cmd --zone="${zone}" --add-port="${port}" >/dev/null
    log "restored runtime allowance: ${port}"
  elif [[ "${wanted}" == "false" ]] && has_exact_runtime_port "${zone}" "${port}"; then
    firewall-cmd --zone="${zone}" --remove-port="${port}" >/dev/null
    log "removed runtime rule absent from the saved state: ${port}"
  fi
}

rollback_hardening() {
  local requested_state_file="${STATE_FILE}"

  require_root
  load_state
  STATE_FILE="${requested_state_file}"

  restore_if_true "${ZONE}" "1025-65535/tcp" "${PERMANENT_TCP_HIGH}"
  restore_if_true "${ZONE}" "1025-65535/udp" "${PERMANENT_UDP_HIGH}"
  restore_if_true "${ZONE}" "${WIREGUARD_PORT}/udp" "${PERMANENT_WIREGUARD}"

  firewall-cmd --reload >/dev/null
  restore_runtime_if_true "${ZONE}" "1025-65535/tcp" "${RUNTIME_TCP_HIGH}"
  restore_runtime_if_true "${ZONE}" "1025-65535/udp" "${RUNTIME_UDP_HIGH}"
  restore_runtime_if_true "${ZONE}" "${WIREGUARD_PORT}/udp" "${RUNTIME_WIREGUARD}"
  log "rollback applied from ${STATE_FILE}"
  report_state "${ZONE}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      MODE="check"
      shift
      ;;
    --apply)
      MODE="apply"
      shift
      ;;
    --rollback)
      MODE="rollback"
      shift
      ;;
    --interface)
      [[ $# -ge 2 ]] || fail "--interface requires a value"
      INTERFACE="$2"
      shift 2
      ;;
    --zone)
      [[ $# -ge 2 ]] || fail "--zone requires a value"
      ZONE="$2"
      shift 2
      ;;
    --wireguard-port)
      [[ $# -ge 2 ]] || fail "--wireguard-port requires a value"
      WIREGUARD_PORT="$2"
      shift 2
      ;;
    --state-file)
      [[ $# -ge 2 ]] || fail "--state-file requires a value"
      STATE_FILE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

require_command firewall-cmd
require_command systemctl
validate_port "${WIREGUARD_PORT}"

if ! systemctl is-active --quiet firewalld.service; then
  fail "firewalld is not running"
fi

case "${MODE}" in
  check)
    ZONE="$(detect_zone)"
    report_state "${ZONE}"
    ;;
  apply)
    ZONE="$(detect_zone)"
    apply_hardening "${ZONE}"
    ;;
  rollback)
    rollback_hardening
    ;;
  *)
    fail "unsupported mode: ${MODE}"
    ;;
esac
