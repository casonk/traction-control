#!/usr/bin/env bash
# mount_snowbridge.sh — mount the snowbridge SMB share on macOS.
#
# Reads connection details from config/keepass-snowbridge.env.local.
# SMB credentials are pulled from macOS Keychain at runtime; no passwords
# are stored in the config file or printed to the terminal.
#
# Usage:
#   bash scripts/mount_snowbridge.sh [--host <hostname-or-ip>]
#
# --host overrides SNOWBRIDGE_HOST for one-off remote connections (e.g. the
# WireGuard tunnel IP) without editing the local config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${REPO_ROOT}/config/keepass-snowbridge.env.local"
EXAMPLE_CONFIG="${REPO_ROOT}/config/keepass-snowbridge.example.env"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '[mount_snowbridge] %s\n' "$*"; }

[[ "$(uname -s)" == "Darwin" ]] || die "this script is for macOS only"

[[ -f "${CONFIG_FILE}" ]] || die "local config not found: ${CONFIG_FILE}
Copy ${EXAMPLE_CONFIG} to ${CONFIG_FILE}, fill in values, then run:
  bash scripts/setup_keepass_snowbridge.sh"

# shellcheck source=/dev/null
source "${CONFIG_FILE}"

: "${SNOWBRIDGE_SMB_USER:?set SNOWBRIDGE_SMB_USER in ${CONFIG_FILE}}"
: "${SNOWBRIDGE_SMB_SHARE:?set SNOWBRIDGE_SMB_SHARE in ${CONFIG_FILE}}"
SNOWBRIDGE_MOUNT_POINT="${SNOWBRIDGE_MOUNT_POINT:-${HOME}/mnt/snowbridge}"

# --host flag overrides the config value for one-off connections
HOST_OVERRIDE=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST_OVERRIDE="$2"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

EFFECTIVE_HOST="${HOST_OVERRIDE:-${SNOWBRIDGE_HOST:-}}"
[[ -n "${EFFECTIVE_HOST}" ]] || die "SNOWBRIDGE_HOST not set in ${CONFIG_FILE} and --host not provided"

# Idempotent — exit quietly if already mounted
if mount | grep -qF " ${SNOWBRIDGE_MOUNT_POINT} "; then
    log "already mounted at ${SNOWBRIDGE_MOUNT_POINT}"
    exit 0
fi

# Pull the SMB password from macOS Keychain (added by setup_keepass_snowbridge.sh).
# Always look up by the canonical SNOWBRIDGE_HOST from config, not the effective
# mount target — so --host <vpn-ip> still finds the credential stored under the
# LAN hostname.
KEYCHAIN_HOST="${SNOWBRIDGE_HOST:?set SNOWBRIDGE_HOST in ${CONFIG_FILE}}"
SMB_PASSWORD="$(
    security find-internet-password \
        -s "${KEYCHAIN_HOST}" \
        -a "${SNOWBRIDGE_SMB_USER}" \
        -w 2>/dev/null
)" || die "SMB credentials not found in Keychain for ${SNOWBRIDGE_SMB_USER}@${KEYCHAIN_HOST}.
Run: bash scripts/setup_keepass_snowbridge.sh"

# URL-encode the password so special characters survive the smb:// URL
SMB_PASSWORD_ENCODED="$(
    python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' \
        "${SMB_PASSWORD}"
)"

mkdir -p "${SNOWBRIDGE_MOUNT_POINT}"

log "mounting //${SNOWBRIDGE_SMB_USER}@${EFFECTIVE_HOST}/${SNOWBRIDGE_SMB_SHARE} → ${SNOWBRIDGE_MOUNT_POINT}"

/sbin/mount_smbfs \
    "//${SNOWBRIDGE_SMB_USER}:${SMB_PASSWORD_ENCODED}@${EFFECTIVE_HOST}/${SNOWBRIDGE_SMB_SHARE}" \
    "${SNOWBRIDGE_MOUNT_POINT}"

log "mounted ok"

# Show vault paths if configured
if [[ -n "${KEEPASS_VAULT_SUBPATHS:-}" ]]; then
    log "configured vaults:"
    for subpath in ${KEEPASS_VAULT_SUBPATHS}; do
        full="${SNOWBRIDGE_MOUNT_POINT}/${subpath}"
        if [[ -f "${full}" ]]; then
            printf '  ✓ %s\n' "${full}"
        else
            printf '  ? %s  (not found — check path)\n' "${full}"
        fi
    done
fi
