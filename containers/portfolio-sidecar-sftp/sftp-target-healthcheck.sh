#!/usr/bin/env bash
# Validate the running sshd and its immutable SFTP-only configuration.

set -euo pipefail

port="${SIDECAR_SFTP_PORT:-2222}"
[[ "${port}" =~ ^[0-9]+$ ]]
((${#port} <= 5))
((10#${port} >= 1024 && 10#${port} <= 65535))
kill -0 1
[[ "$(readlink /proc/1/exe)" == /usr/sbin/sshd ]]
/usr/sbin/sshd -t -f /etc/ssh/sshd_config -o "Port=${port}"
