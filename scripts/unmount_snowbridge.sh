#!/usr/bin/env bash
# unmount_snowbridge.sh — cleanly unmount the snowbridge SMB share on macOS.
#
# Uses diskutil to unmount so macOS can flush caches and close open files
# before the SMB session is torn down.
#
# Usage:
#   bash scripts/unmount_snowbridge.sh [--force]
#
# --force passes the 'force' flag to diskutil unmount (use if normal unmount
# reports "resource busy" and you have confirmed no open files).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${REPO_ROOT}/config/keepass-snowbridge.env.local"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '[unmount_snowbridge] %s\n' "$*"; }

[[ "$(uname -s)" == "Darwin" ]] || die "this script is for macOS only"

SNOWBRIDGE_MOUNT_POINT="${HOME}/mnt/snowbridge"
FORCE=

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=force; shift ;;
        *) die "unknown argument: $1" ;;
    esac
done

# Read mount point from config if available
if [[ -f "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
    SNOWBRIDGE_MOUNT_POINT="${SNOWBRIDGE_MOUNT_POINT:-/Volumes/snowbridge}"
fi

if ! mount | grep -qF " ${SNOWBRIDGE_MOUNT_POINT} "; then
    log "not mounted at ${SNOWBRIDGE_MOUNT_POINT} — nothing to do"
    exit 0
fi

log "unmounting ${SNOWBRIDGE_MOUNT_POINT}${FORCE:+ (force)}"
diskutil unmount ${FORCE} "${SNOWBRIDGE_MOUNT_POINT}"
log "unmounted ok"
