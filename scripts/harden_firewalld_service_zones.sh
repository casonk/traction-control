#!/usr/bin/env bash
set -euo pipefail

MODE="check"
LAN_INTERFACE="enp5s0"
LAN_ZONE=""
VPN_INTERFACE="wg0"
VPN_ZONE="wireguard"
LEGACY_VPN_ZONE="trusted"
VPN_SOURCE="10.99.0.0/24"
TRANSMISSION_PEER_PORT="51413"
STATE_FILE="/var/lib/traction-control/firewalld-service-zones.env"

VPN_SERVICES=(ssh samba dns http https cockpit)
VPN_PORTS=(3389/tcp 443/udp)
LAN_SERVICES=(ssh samba)
LAN_PEER_PORTS=("${TRANSMISSION_PEER_PORT}/tcp" "${TRANSMISSION_PEER_PORT}/udp")

usage() {
  cat <<'EOF'
Usage: harden_firewalld_service_zones.sh [MODE] [OPTIONS]

Replace an unrestricted trusted-zone WireGuard assignment with an explicit
service allowlist, while preserving selected LAN services and Transmission peer
ingress.

Modes:
  --check      Report current service-zone boundaries (default).
  --apply      Snapshot targeted state, apply restrictions, and verify them.
  --rollback   Restore the targeted state recorded by the last --apply.

Options:
  --lan-interface NAME       LAN/WAN interface (default: enp5s0).
  --lan-zone NAME            Override LAN zone auto-detection.
  --vpn-interface NAME       WireGuard interface (default: wg0).
  --vpn-zone NAME            Restricted VPN zone (default: wireguard).
  --legacy-vpn-zone NAME     Current unrestricted zone (default: trusted).
  --vpn-source CIDR          WireGuard client subnet (default: 10.99.0.0/24).
  --transmission-port PORT   Peer TCP/UDP port to expose (default: 51413).
  --state-file PATH          Root-owned rollback state file.
  --help                     Show this help.

Examples:
  bash scripts/harden_firewalld_service_zones.sh --check
  sudo bash scripts/harden_firewalld_service_zones.sh --apply
  sudo bash scripts/harden_firewalld_service_zones.sh --rollback
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
  [[ "$1" =~ ^[0-9]+$ ]] || fail "invalid port: $1"
  (( 10#$1 >= 1 && 10#$1 <= 65535 )) || fail "port must be between 1 and 65535"
}

require_option_value() {
  local option="$1"
  local value="${2:-}"

  [[ -n "${value}" && "${value}" != --* ]] || fail "${option} requires a value"
}

list_has_exact_item() {
  local item_list="$1"
  local wanted="$2"
  local listed

  for listed in ${item_list}; do
    [[ "${listed}" == "${wanted}" ]] && return 0
  done
  return 1
}

runtime_has_service() {
  list_has_exact_item "$(firewall-cmd --zone="$1" --list-services)" "$2"
}

permanent_has_service() {
  list_has_exact_item "$(firewall-cmd --permanent --zone="$1" --list-services)" "$2"
}

runtime_has_port() {
  list_has_exact_item "$(firewall-cmd --zone="$1" --list-ports)" "$2"
}

permanent_has_port() {
  list_has_exact_item "$(firewall-cmd --permanent --zone="$1" --list-ports)" "$2"
}

runtime_has_source() {
  list_has_exact_item "$(firewall-cmd --zone="$1" --list-sources)" "$2"
}

permanent_has_source() {
  list_has_exact_item "$(firewall-cmd --permanent --zone="$1" --list-sources)" "$2"
}

runtime_forward_enabled() {
  firewall-cmd --zone="$1" --query-forward >/dev/null 2>&1
}

permanent_forward_enabled() {
  firewall-cmd --permanent --zone="$1" --query-forward >/dev/null 2>&1
}

bool_for() {
  if "$@"; then
    printf 'true\n'
  else
    printf 'false\n'
  fi
}

detect_lan_zone() {
  local detected

  if [[ -n "${LAN_ZONE}" ]]; then
    printf '%s\n' "${LAN_ZONE}"
    return
  fi

  detected="$(firewall-cmd --get-zone-of-interface="${LAN_INTERFACE}" 2>/dev/null || true)"
  [[ -n "${detected}" ]] || fail "no active firewalld zone found for ${LAN_INTERFACE}"
  printf '%s\n' "${detected}"
}

report_state() {
  local current_vpn_zone

  current_vpn_zone="$(firewall-cmd --get-zone-of-interface="${VPN_INTERFACE}" 2>/dev/null || true)"
  log "LAN interface: ${LAN_INTERFACE}"
  log "LAN zone: ${LAN_ZONE}"
  log "LAN services: $(firewall-cmd --zone="${LAN_ZONE}" --list-services)"
  log "LAN ports: $(firewall-cmd --zone="${LAN_ZONE}" --list-ports)"
  log "VPN interface: ${VPN_INTERFACE}"
  log "VPN interface zone: ${current_vpn_zone:-unassigned}"
  log "${VPN_ZONE} target: $(firewall-cmd --permanent --zone="${VPN_ZONE}" --get-target 2>/dev/null || printf 'unavailable')"
  log "${VPN_ZONE} services: $(firewall-cmd --zone="${VPN_ZONE}" --list-services)"
  log "${VPN_ZONE} ports: $(firewall-cmd --zone="${VPN_ZONE}" --list-ports)"
  log "${VPN_ZONE} forwarding: $(bool_for runtime_forward_enabled "${VPN_ZONE}")"
  log "${LEGACY_VPN_ZONE} sources: $(firewall-cmd --zone="${LEGACY_VPN_ZONE}" --list-sources)"

  if (( EUID == 0 )); then
    log "permanent ${VPN_ZONE} services: $(firewall-cmd --permanent --zone="${VPN_ZONE}" --list-services)"
    log "permanent ${VPN_ZONE} ports: $(firewall-cmd --permanent --zone="${VPN_ZONE}" --list-ports)"
    log "permanent ${VPN_ZONE} forwarding: $(bool_for permanent_forward_enabled "${VPN_ZONE}")"
    log "permanent ${LEGACY_VPN_ZONE} sources: $(firewall-cmd --permanent --zone="${LEGACY_VPN_ZONE}" --list-sources)"
    log "permanent LAN ports: $(firewall-cmd --permanent --zone="${LAN_ZONE}" --list-ports)"
  else
    log "permanent state: unavailable without elevated firewalld authorization"
  fi
}

write_state() {
  local state_dir
  local temp_file
  local service
  local port

  if [[ -f "${STATE_FILE}" ]]; then
    log "preserving existing rollback state: ${STATE_FILE}"
    return
  fi

  state_dir="$(dirname "${STATE_FILE}")"
  install -d -m 0700 "${state_dir}"
  temp_file="$(mktemp "${state_dir}/firewalld-service-zones.XXXXXX")"

  {
    printf 'STATE_VERSION=1\n'
    printf 'LAN_INTERFACE=%q\n' "${LAN_INTERFACE}"
    printf 'LAN_ZONE=%q\n' "${LAN_ZONE}"
    printf 'VPN_INTERFACE=%q\n' "${VPN_INTERFACE}"
    printf 'VPN_ZONE=%q\n' "${VPN_ZONE}"
    printf 'LEGACY_VPN_ZONE=%q\n' "${LEGACY_VPN_ZONE}"
    printf 'VPN_SOURCE=%q\n' "${VPN_SOURCE}"
    printf 'TRANSMISSION_PEER_PORT=%q\n' "${TRANSMISSION_PEER_PORT}"
    printf 'ORIGINAL_RUNTIME_VPN_ZONE=%q\n' "$(firewall-cmd --get-zone-of-interface="${VPN_INTERFACE}" 2>/dev/null || true)"
    printf 'ORIGINAL_PERMANENT_VPN_ZONE=%q\n' "$(firewall-cmd --permanent --get-zone-of-interface="${VPN_INTERFACE}" 2>/dev/null || true)"
    printf 'ORIGINAL_RUNTIME_LEGACY_SOURCE=%q\n' "$(bool_for runtime_has_source "${LEGACY_VPN_ZONE}" "${VPN_SOURCE}")"
    printf 'ORIGINAL_PERMANENT_LEGACY_SOURCE=%q\n' "$(bool_for permanent_has_source "${LEGACY_VPN_ZONE}" "${VPN_SOURCE}")"
    printf 'ORIGINAL_PERMANENT_VPN_FORWARD=%q\n' "$(bool_for permanent_forward_enabled "${VPN_ZONE}")"
    printf 'ORIGINAL_PERMANENT_VPN_TARGET=%q\n' "$(firewall-cmd --permanent --zone="${VPN_ZONE}" --get-target)"
    for service in "${VPN_SERVICES[@]}"; do
      printf 'ORIGINAL_SERVICE_%s=%q\n' "${service^^}" "$(bool_for permanent_has_service "${VPN_ZONE}" "${service}")"
    done
    for service in "${LAN_SERVICES[@]}"; do
      printf 'ORIGINAL_LAN_SERVICE_%s=%q\n' "${service^^}" "$(bool_for permanent_has_service "${LAN_ZONE}" "${service}")"
    done
    for port in "${VPN_PORTS[@]}"; do
      printf 'ORIGINAL_VPN_PORT_%s=%q\n' "${port//[^A-Za-z0-9]/_}" "$(bool_for permanent_has_port "${VPN_ZONE}" "${port}")"
    done
    for port in "${LAN_PEER_PORTS[@]}"; do
      printf 'ORIGINAL_LAN_PORT_%s=%q\n' "${port//[^A-Za-z0-9]/_}" "$(bool_for permanent_has_port "${LAN_ZONE}" "${port}")"
    done
  } > "${temp_file}"

  chmod 0600 "${temp_file}"
  mv -f "${temp_file}" "${STATE_FILE}"
  log "saved rollback state: ${STATE_FILE}"
}

ensure_permanent_service() {
  if ! permanent_has_service "$1" "$2"; then
    firewall-cmd --permanent --zone="$1" --add-service="$2" >/dev/null
    log "allowed service in $1: $2"
  fi
}

ensure_permanent_port() {
  if ! permanent_has_port "$1" "$2"; then
    firewall-cmd --permanent --zone="$1" --add-port="$2" >/dev/null
    log "allowed port in $1: $2"
  fi
}

verify_hardened() {
  local current_vpn_zone
  local service
  local port
  local failed=0

  current_vpn_zone="$(firewall-cmd --get-zone-of-interface="${VPN_INTERFACE}" 2>/dev/null || true)"
  if [[ "${current_vpn_zone}" != "${VPN_ZONE}" ]]; then
    printf 'verification failed: %s is in %s, expected %s\n' \
      "${VPN_INTERFACE}" "${current_vpn_zone:-no zone}" "${VPN_ZONE}" >&2
    failed=1
  fi

  if runtime_has_source "${LEGACY_VPN_ZONE}" "${VPN_SOURCE}" ||
     permanent_has_source "${LEGACY_VPN_ZONE}" "${VPN_SOURCE}"; then
    printf 'verification failed: %s still bypasses the allowlist through %s\n' \
      "${VPN_SOURCE}" "${LEGACY_VPN_ZONE}" >&2
    failed=1
  fi

  if [[ "$(firewall-cmd --permanent --zone="${VPN_ZONE}" --get-target)" != "default" ]]; then
    printf 'verification failed: %s target is not default\n' "${VPN_ZONE}" >&2
    failed=1
  fi

  for service in "${VPN_SERVICES[@]}"; do
    if ! runtime_has_service "${VPN_ZONE}" "${service}" ||
       ! permanent_has_service "${VPN_ZONE}" "${service}"; then
      printf 'verification failed: VPN service missing: %s\n' "${service}" >&2
      failed=1
    fi
  done

  for port in "${VPN_PORTS[@]}"; do
    if ! runtime_has_port "${VPN_ZONE}" "${port}" ||
       ! permanent_has_port "${VPN_ZONE}" "${port}"; then
      printf 'verification failed: VPN port missing: %s\n' "${port}" >&2
      failed=1
    fi
  done

  if ! runtime_forward_enabled "${VPN_ZONE}" ||
     ! permanent_forward_enabled "${VPN_ZONE}"; then
    printf 'verification failed: VPN forwarding is disabled\n' >&2
    failed=1
  fi

  for service in "${LAN_SERVICES[@]}"; do
    if ! runtime_has_service "${LAN_ZONE}" "${service}" ||
       ! permanent_has_service "${LAN_ZONE}" "${service}"; then
      printf 'verification failed: LAN service missing: %s\n' "${service}" >&2
      failed=1
    fi
  done

  for port in "${LAN_PEER_PORTS[@]}"; do
    if ! runtime_has_port "${LAN_ZONE}" "${port}" ||
       ! permanent_has_port "${LAN_ZONE}" "${port}"; then
      printf 'verification failed: Transmission peer port missing: %s\n' "${port}" >&2
      failed=1
    fi
  done

  (( failed == 0 )) || return 1
  log "verification passed: VPN ingress uses an explicit allowlist and Transmission peer ingress is explicit"
}

apply_hardening() {
  local service
  local port

  require_root
  write_state

  if [[ "$(firewall-cmd --permanent --zone="${VPN_ZONE}" --get-target)" != "default" ]]; then
    firewall-cmd --permanent --zone="${VPN_ZONE}" --set-target=default >/dev/null
    log "set ${VPN_ZONE} target to default"
  fi

  for service in "${VPN_SERVICES[@]}"; do
    ensure_permanent_service "${VPN_ZONE}" "${service}"
  done
  for port in "${VPN_PORTS[@]}"; do
    ensure_permanent_port "${VPN_ZONE}" "${port}"
  done
  if ! permanent_forward_enabled "${VPN_ZONE}"; then
    firewall-cmd --permanent --zone="${VPN_ZONE}" --add-forward >/dev/null
    log "enabled forwarding in ${VPN_ZONE}"
  fi

  for port in "${LAN_PEER_PORTS[@]}"; do
    ensure_permanent_port "${LAN_ZONE}" "${port}"
  done

  for service in "${LAN_SERVICES[@]}"; do
    ensure_permanent_service "${LAN_ZONE}" "${service}"
  done

  if permanent_has_source "${LEGACY_VPN_ZONE}" "${VPN_SOURCE}"; then
    firewall-cmd --permanent --zone="${LEGACY_VPN_ZONE}" --remove-source="${VPN_SOURCE}" >/dev/null
    log "removed unrestricted source from ${LEGACY_VPN_ZONE}: ${VPN_SOURCE}"
  fi

  firewall-cmd --permanent --zone="${VPN_ZONE}" --change-interface="${VPN_INTERFACE}" >/dev/null
  log "assigned ${VPN_INTERFACE} to restricted zone ${VPN_ZONE}"
  firewall-cmd --reload >/dev/null

  verify_hardened
  report_state
}

load_state() {
  local owner
  local mode
  local saved_bool

  [[ -f "${STATE_FILE}" ]] || fail "rollback state does not exist: ${STATE_FILE}"
  owner="$(stat -c '%u' "${STATE_FILE}")"
  mode="$(stat -c '%a' "${STATE_FILE}")"
  [[ "${owner}" == "0" ]] || fail "rollback state must be owned by root"
  (( (8#${mode} & 8#022) == 0 )) || fail "rollback state must not be group- or world-writable"
  # shellcheck source=/dev/null
  source "${STATE_FILE}"
  [[ "${STATE_VERSION:-}" == "1" ]] || fail "unsupported rollback state version"
  [[ "${LAN_INTERFACE:-}" =~ ^[A-Za-z0-9_.:-]+$ ]] || fail "invalid LAN interface in rollback state"
  [[ "${LAN_ZONE:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "invalid LAN zone in rollback state"
  [[ "${VPN_INTERFACE:-}" =~ ^[A-Za-z0-9_.:-]+$ ]] || fail "invalid VPN interface in rollback state"
  [[ "${VPN_ZONE:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "invalid VPN zone in rollback state"
  [[ "${LEGACY_VPN_ZONE:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "invalid legacy VPN zone in rollback state"
  [[ "${VPN_SOURCE:-}" =~ ^[A-Za-z0-9_:.\/-]+$ ]] || fail "invalid VPN source in rollback state"
  validate_port "${TRANSMISSION_PEER_PORT:-}"
  for saved_bool in \
    "${ORIGINAL_RUNTIME_LEGACY_SOURCE:-}" \
    "${ORIGINAL_PERMANENT_LEGACY_SOURCE:-}" \
    "${ORIGINAL_PERMANENT_VPN_FORWARD:-}"; do
    [[ "${saved_bool}" == "true" || "${saved_bool}" == "false" ]] ||
      fail "invalid boolean in rollback state"
  done
  LAN_PEER_PORTS=("${TRANSMISSION_PEER_PORT}/tcp" "${TRANSMISSION_PEER_PORT}/udp")
}

restore_permanent_service() {
  local variable="ORIGINAL_SERVICE_${2^^}"
  local wanted="${!variable}"

  if [[ "${wanted}" == "true" ]]; then
    ensure_permanent_service "$1" "$2"
  elif permanent_has_service "$1" "$2"; then
    firewall-cmd --permanent --zone="$1" --remove-service="$2" >/dev/null
    log "removed service absent from saved state in $1: $2"
  fi
}

restore_lan_service() {
  local variable="ORIGINAL_LAN_SERVICE_${2^^}"
  local wanted="${!variable}"

  if [[ "${wanted}" == "true" ]]; then
    ensure_permanent_service "$1" "$2"
  elif permanent_has_service "$1" "$2"; then
    firewall-cmd --permanent --zone="$1" --remove-service="$2" >/dev/null
    log "removed LAN service absent from saved state in $1: $2"
  fi
}

restore_permanent_port() {
  local prefix="$1"
  local zone="$2"
  local port="$3"
  local variable="${prefix}_${port//[^A-Za-z0-9]/_}"
  local wanted="${!variable}"

  if [[ "${wanted}" == "true" ]]; then
    ensure_permanent_port "${zone}" "${port}"
  elif permanent_has_port "${zone}" "${port}"; then
    firewall-cmd --permanent --zone="${zone}" --remove-port="${port}" >/dev/null
    log "removed port absent from saved state in ${zone}: ${port}"
  fi
}

rollback_hardening() {
  local requested_state_file="${STATE_FILE}"
  local service
  local port

  require_root
  load_state
  STATE_FILE="${requested_state_file}"

  for service in "${VPN_SERVICES[@]}"; do
    restore_permanent_service "${VPN_ZONE}" "${service}"
  done
  for service in "${LAN_SERVICES[@]}"; do
    restore_lan_service "${LAN_ZONE}" "${service}"
  done
  for port in "${VPN_PORTS[@]}"; do
    restore_permanent_port ORIGINAL_VPN_PORT "${VPN_ZONE}" "${port}"
  done
  for port in "${LAN_PEER_PORTS[@]}"; do
    restore_permanent_port ORIGINAL_LAN_PORT "${LAN_ZONE}" "${port}"
  done

  if [[ "${ORIGINAL_PERMANENT_VPN_FORWARD}" == "true" ]]; then
    firewall-cmd --permanent --zone="${VPN_ZONE}" --add-forward >/dev/null
  else
    firewall-cmd --permanent --zone="${VPN_ZONE}" --remove-forward >/dev/null 2>&1 || true
  fi
  firewall-cmd --permanent --zone="${VPN_ZONE}" --set-target="${ORIGINAL_PERMANENT_VPN_TARGET}" >/dev/null

  if [[ "${ORIGINAL_PERMANENT_LEGACY_SOURCE}" == "true" ]]; then
    firewall-cmd --permanent --zone="${LEGACY_VPN_ZONE}" --add-source="${VPN_SOURCE}" >/dev/null
  else
    firewall-cmd --permanent --zone="${LEGACY_VPN_ZONE}" --remove-source="${VPN_SOURCE}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${ORIGINAL_PERMANENT_VPN_ZONE}" ]]; then
    firewall-cmd --permanent --zone="${ORIGINAL_PERMANENT_VPN_ZONE}" --change-interface="${VPN_INTERFACE}" >/dev/null
  else
    firewall-cmd --permanent --zone="${VPN_ZONE}" --remove-interface="${VPN_INTERFACE}" >/dev/null 2>&1 || true
  fi

  firewall-cmd --reload >/dev/null
  if [[ -n "${ORIGINAL_RUNTIME_VPN_ZONE}" &&
        "${ORIGINAL_RUNTIME_VPN_ZONE}" != "${ORIGINAL_PERMANENT_VPN_ZONE}" ]]; then
    firewall-cmd --zone="${ORIGINAL_RUNTIME_VPN_ZONE}" --change-interface="${VPN_INTERFACE}" >/dev/null
  fi
  if [[ "${ORIGINAL_RUNTIME_LEGACY_SOURCE}" == "true" &&
        "${ORIGINAL_PERMANENT_LEGACY_SOURCE}" == "false" ]]; then
    firewall-cmd --zone="${LEGACY_VPN_ZONE}" --add-source="${VPN_SOURCE}" >/dev/null
  fi
  log "rollback applied from ${STATE_FILE}"
  report_state
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --apply) MODE="apply"; shift ;;
    --rollback) MODE="rollback"; shift ;;
    --lan-interface) require_option_value "$1" "${2:-}"; LAN_INTERFACE="$2"; shift 2 ;;
    --lan-zone) require_option_value "$1" "${2:-}"; LAN_ZONE="$2"; shift 2 ;;
    --vpn-interface) require_option_value "$1" "${2:-}"; VPN_INTERFACE="$2"; shift 2 ;;
    --vpn-zone) require_option_value "$1" "${2:-}"; VPN_ZONE="$2"; shift 2 ;;
    --legacy-vpn-zone) require_option_value "$1" "${2:-}"; LEGACY_VPN_ZONE="$2"; shift 2 ;;
    --vpn-source) require_option_value "$1" "${2:-}"; VPN_SOURCE="$2"; shift 2 ;;
    --transmission-port) require_option_value "$1" "${2:-}"; TRANSMISSION_PEER_PORT="$2"; shift 2 ;;
    --state-file) require_option_value "$1" "${2:-}"; STATE_FILE="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

require_command firewall-cmd
require_command systemctl
validate_port "${TRANSMISSION_PEER_PORT}"
LAN_PEER_PORTS=("${TRANSMISSION_PEER_PORT}/tcp" "${TRANSMISSION_PEER_PORT}/udp")

systemctl is-active --quiet firewalld.service || fail "firewalld is not running"

case "${MODE}" in
  check)
    LAN_ZONE="$(detect_lan_zone)"
    report_state
    ;;
  apply)
    LAN_ZONE="$(detect_lan_zone)"
    apply_hardening
    ;;
  rollback) rollback_hardening ;;
  *) fail "unsupported mode: ${MODE}" ;;
esac
