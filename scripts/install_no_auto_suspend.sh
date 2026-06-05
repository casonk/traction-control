#!/usr/bin/env bash
# install_no_auto_suspend.sh — install a system-level sleep block inhibitor so
# that Caddy, Samba, Flask services, and WireGuard stay online indefinitely.
#
# Root cause this fixes: gsd-power running under the gdm-greeter session reads
# the gdm user's dconf (not the desktop user's), and if UPower reports an
# unknown power state the greeter falls back to the 900 s battery-idle-suspend
# policy, suspending the machine and dropping all network services.
#
# The fix creates /etc/systemd/system/no-auto-suspend.service, which holds an
# open logind block-mode inhibitor on "sleep" for the lifetime of the machine.
# A block-mode inhibitor prevents any caller — including gsd-power — from
# suspending the system via systemd-logind, regardless of which GNOME session
# owns the request.

set -euo pipefail

UNIT_NAME="no-auto-suspend.service"
UNIT_DEST="/etc/systemd/system/${UNIT_NAME}"
TMP_UNIT="$(mktemp /tmp/${UNIT_NAME}.XXXXXX)"
UNINSTALL=0

usage() {
  cat <<EOF
Usage: install_no_auto_suspend.sh [--uninstall] [--help]

Options:
  --uninstall   Stop and remove the service (reverse this installation).
  --help        Show this message.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) UNINSTALL=1; shift ;;
    --help|-h)   usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

command -v systemd-inhibit >/dev/null 2>&1 || fail "systemd-inhibit not found — is systemd installed?"

# ── uninstall ────────────────────────────────────────────────────────────────
if (( UNINSTALL == 1 )); then
  if ! [[ -f "${UNIT_DEST}" ]]; then
    printf 'nothing to remove: %s does not exist\n' "${UNIT_DEST}"
    exit 0
  fi
  printf 'Stopping and disabling %s …\n' "${UNIT_NAME}"
  sudo systemctl disable --now "${UNIT_NAME}"
  sudo rm -f "${UNIT_DEST}"
  sudo systemctl daemon-reload
  printf 'Removed %s\n' "${UNIT_DEST}"
  exit 0
fi

# ── install ──────────────────────────────────────────────────────────────────
trap 'rm -f "${TMP_UNIT}"' EXIT

cat > "${TMP_UNIT}" <<'UNIT'
[Unit]
Description=Block automatic system suspend for persistent services
DefaultDependencies=no
After=sysinit.target

[Service]
Type=simple
ExecStart=/usr/bin/systemd-inhibit \
    --what=sleep \
    --who=desk-local-services \
    --why=persistent-network-services \
    --mode=block \
    sleep infinity
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

printf 'Installing %s …\n' "${UNIT_DEST}"
sudo cp "${TMP_UNIT}" "${UNIT_DEST}"
sudo chmod 644 "${UNIT_DEST}"
sudo systemctl daemon-reload
sudo systemctl stop "${UNIT_NAME}" 2>/dev/null || true
sudo systemctl reset-failed "${UNIT_NAME}" 2>/dev/null || true
sudo systemctl enable "${UNIT_NAME}"
sudo systemctl start "${UNIT_NAME}"

# ── verify ───────────────────────────────────────────────────────────────────
printf '\nService status:\n'
systemctl status "${UNIT_NAME}" --no-pager -l

printf '\nActive sleep inhibitors:\n'
systemd-inhibit --list | head -1          # header
systemd-inhibit --list | grep -i "sleep\|suspend" || true

printf '\nDone. The block inhibitor is active and will re-arm on every boot.\n'
printf 'To reverse: %s --uninstall\n' "$(basename "$0")"
