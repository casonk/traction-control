#!/usr/bin/env bash
# Start one disposable key-only OpenSSH target for the real sidecar regression.

set -euo pipefail

KEY_PREFIX="${SIDECAR_TARGET_KEY_PREFIX:?SIDECAR_TARGET_KEY_PREFIX is required}"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[[ "${KEY_PREFIX}" =~ ^(hosted|mesh_1|mesh_2|mesh_3)$ ]] \
  || fail "synthetic SSH key prefix is invalid"
[[ -s "/test-fixture/keys/${KEY_PREFIX}_host_ed25519" ]] \
  || fail "synthetic SSH host private key is missing"
[[ -s "/test-fixture/keys/${KEY_PREFIX}_host_ed25519.pub" ]] \
  || fail "synthetic SSH host public key is missing"
[[ -s /test-fixture/keys/client_ed25519.pub ]] \
  || fail "synthetic client public key is missing"

install -d -m 0755 /run/sshd
install -d -m 0755 /run/sshd/authorized_keys
install -m 0600 \
  "/test-fixture/keys/${KEY_PREFIX}_host_ed25519" \
  /run/sshd/ssh_host_ed25519_key
install -m 0644 \
  "/test-fixture/keys/${KEY_PREFIX}_host_ed25519.pub" \
  /run/sshd/ssh_host_ed25519_key.pub
install -m 0644 \
  /test-fixture/keys/client_ed25519.pub \
  /run/sshd/authorized_keys/sidecarbackup
chown sidecarbackup:sidecarbackup /home/sidecarbackup/repository

/usr/sbin/sshd -t -f /etc/ssh/sshd_config
exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
