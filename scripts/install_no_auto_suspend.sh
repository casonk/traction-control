#!/usr/bin/env bash
# LEGACY — this script is superseded and the service it installs does not work.
#
# Why it was written:
#   gsd-power running under the GDM greeter session reads the gdm user's dconf
#   (not the desktop user's). When UPower reports an unknown power state, GDM
#   falls back to the 900 s battery-idle-suspend policy, suspending the machine
#   and dropping Caddy, Samba, Flask services, and WireGuard tunnels.
#
# Why it does not work:
#   systemd-inhibit --mode=block requires the polkit action
#   org.freedesktop.login1.inhibit-block-sleep with interactive authentication.
#   A system service (root, no active session) is denied by polkit even on
#   Fedora with default rules. The service fails immediately on every boot and
#   loops through 224+ restarts before systemd's rate-limit disables it.
#   Observed error: "systemd-inhibit: must failed with exit status 1".
#
# Correct fix (already applied 2026-06-05):
#   Install a GDM-specific dconf policy via
#   ./util-repos/fedora-debugg/scripts/install_gdm_no_auto_suspend.sh
#   That script writes /etc/dconf/db/gdm.d/99-fedora-debugg-disable-auto-suspend
#   and locks it, preventing GDM from suspending regardless of UPower state.
#   Combine with the user-session gsettings fix:
#     gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'
#     gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0
#
# Current status:
#   no-auto-suspend.service is disabled and inactive. The unit file at
#   /etc/systemd/system/no-auto-suspend.service is kept for reference only.
#   Run this script with --uninstall to remove the unit file if desired.

set -euo pipefail

UNIT_NAME="no-auto-suspend.service"
UNIT_DEST="/etc/systemd/system/${UNIT_NAME}"
UNINSTALL=0

usage() {
  cat <<EOF
Usage: install_no_auto_suspend.sh [--uninstall] [--help]

LEGACY — this script no longer installs anything. See the header for context.

Options:
  --uninstall   Remove the legacy unit file if it still exists on disk.
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

# ── uninstall ────────────────────────────────────────────────────────────────
if (( UNINSTALL == 1 )); then
  if ! [[ -f "${UNIT_DEST}" ]]; then
    printf 'nothing to remove: %s does not exist\n' "${UNIT_DEST}"
    exit 0
  fi
  printf 'Stopping and disabling %s …\n' "${UNIT_NAME}"
  sudo systemctl disable --now "${UNIT_NAME}" 2>/dev/null || true
  sudo rm -f "${UNIT_DEST}"
  sudo systemctl daemon-reload
  printf 'Removed %s\n' "${UNIT_DEST}"
  exit 0
fi

# ── install path is disabled ─────────────────────────────────────────────────
cat <<'EOF'
LEGACY: this script no longer installs no-auto-suspend.service.

The systemd-inhibit --mode=block approach does not work from a system service
because polkit denies the inhibitor-block-sleep action without interactive auth.

Use the GDM dconf fix instead:
  sudo ./util-repos/fedora-debugg/scripts/install_gdm_no_auto_suspend.sh

And verify the user-session gsettings are set:
  gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type
  gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
  (both should be 'nothing')

To remove the legacy unit file from disk:
  bash scripts/install_no_auto_suspend.sh --uninstall
EOF
exit 0
