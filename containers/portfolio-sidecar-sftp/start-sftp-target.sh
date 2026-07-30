#!/usr/bin/env bash
# Start one fail-closed, key-only SFTP target under rootless Podman.

set -euo pipefail
umask 077

readonly TARGET_USER="sidecarbackup"
readonly TARGET_GROUP="sidecarbackup"
readonly REPOSITORY_ROOT="/srv/portfolio-sidecar/repository"
readonly RUNTIME_ROOT="/run/sshd"
readonly RUNTIME_HOST_KEY="${RUNTIME_ROOT}/ssh_host_ed25519_key"
readonly RUNTIME_AUTHORIZED_KEYS="${RUNTIME_ROOT}/authorized_keys"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

validate_port() {
  local value="$1"
  [[ "${value}" =~ ^[0-9]+$ ]] \
    || fail "SIDECAR_SFTP_PORT must be an integer from 1024 through 65535"
  ((${#value} <= 5)) \
    || fail "SIDECAR_SFTP_PORT must be an integer from 1024 through 65535"
  ((10#${value} >= 1024 && 10#${value} <= 65535)) \
    || fail "SIDECAR_SFTP_PORT must be an integer from 1024 through 65535"
}

validate_secret_source() {
  local label="$1"
  local source_path="$2"
  local maximum_bytes="$3"
  local source_links
  local source_mode
  local source_owner
  local source_size

  [[ "${source_path}" == /* ]] \
    || fail "${label} secret path must be absolute"
  [[ -f "${source_path}" && ! -L "${source_path}" ]] \
    || fail "${label} secret must be a regular non-symlink file"
  [[ -r "${source_path}" && -s "${source_path}" ]] \
    || fail "${label} secret must be readable and non-empty"

  source_owner="$(stat --format='%u' -- "${source_path}")" \
    || fail "could not inspect ${label} secret owner"
  source_links="$(stat --format='%h' -- "${source_path}")" \
    || fail "could not inspect ${label} secret links"
  source_mode="$(stat --format='%a' -- "${source_path}")" \
    || fail "could not inspect ${label} secret mode"
  source_size="$(stat --format='%s' -- "${source_path}")" \
    || fail "could not inspect ${label} secret"
  [[ "${source_owner}" == "0" ]] \
    || fail "${label} secret must be owned by container root (uid ${source_owner})"
  [[ "${source_links}" == "1" ]] \
    || fail "${label} secret must have exactly one hard link (found ${source_links})"
  [[ "${source_mode}" =~ ^[0-7]{3,4}$ ]] \
    || fail "could not inspect ${label} secret permissions"
  (((8#${source_mode} & 8#077) == 0)) \
    || fail "${label} secret must not be accessible by group or other users (mode ${source_mode})"
  [[ "${source_size}" =~ ^[0-9]+$ ]] \
    || fail "could not inspect ${label} secret size"
  ((source_size <= maximum_bytes)) \
    || fail "${label} secret exceeds ${maximum_bytes} bytes"
}

prepare_runtime_root() {
  [[ -d /run && -w /run && ! -L /run ]] \
    || fail "/run must be a writable tmpfs directory"
  if [[ -e "${RUNTIME_ROOT}" || -L "${RUNTIME_ROOT}" ]]; then
    [[ -d "${RUNTIME_ROOT}" && ! -L "${RUNTIME_ROOT}" ]] \
      || fail "${RUNTIME_ROOT} must be a real directory"
    [[ -z "$(find "${RUNTIME_ROOT}" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
      || fail "${RUNTIME_ROOT} must be empty at startup"
  else
    install -d -m 0755 "${RUNTIME_ROOT}"
  fi
  install -d -m 0755 "${RUNTIME_ROOT}/empty"
}

prepare_host_key() {
  local source_path="$1"
  local key_type

  install -m 0600 -- "${source_path}" "${RUNTIME_HOST_KEY}"
  key_type="$(ssh-keygen -y -f "${RUNTIME_HOST_KEY}" | awk 'NR == 1 {print $1}')" \
    || fail "SFTP host private key is invalid"
  [[ "${key_type}" == "ssh-ed25519" ]] \
    || fail "SFTP host private key must be Ed25519"
  ssh-keygen -y -f "${RUNTIME_HOST_KEY}" \
    >"${RUNTIME_HOST_KEY}.pub" \
    || fail "could not derive SFTP host public key"
  chmod 0644 "${RUNTIME_HOST_KEY}.pub"
}

prepare_authorized_keys() {
  local source_path="$1"
  local key_blob
  local key_type
  local line
  local line_number=0
  local key_count=0
  local validation_file="${RUNTIME_ROOT}/authorized_keys.validation"

  : >"${RUNTIME_AUTHORIZED_KEYS}"
  : >"${validation_file}"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line_number=$((line_number + 1))
    [[ -n "${line}" && "${line}" != *$'\r'* ]] \
      || fail "authorized_keys line ${line_number} is empty or malformed"
    IFS=' ' read -r key_type key_blob _ <<<"${line}"
    [[ "${key_type}" == "ssh-ed25519" ]] \
      || fail "authorized_keys line ${line_number} must contain a plain Ed25519 key"
    [[ "${key_blob}" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] \
      || fail "authorized_keys line ${line_number} has malformed key data"
    printf '%s %s\n' "${key_type}" "${key_blob}" >>"${validation_file}"
    printf 'restrict %s %s\n' "${key_type}" "${key_blob}" \
      >>"${RUNTIME_AUTHORIZED_KEYS}"
    key_count=$((key_count + 1))
  done <"${source_path}"
  ((key_count > 0)) || fail "authorized_keys must contain at least one key"

  ssh-keygen -l -f "${validation_file}" >/dev/null \
    || fail "authorized_keys contains an invalid key"
  [[ "$(sort -u "${validation_file}" | wc -l | tr -d ' ')" == "${key_count}" ]] \
    || fail "authorized_keys contains duplicate keys"
  chmod 0600 "${RUNTIME_AUTHORIZED_KEYS}"
  chown "${TARGET_USER}:${TARGET_GROUP}" "${RUNTIME_AUTHORIZED_KEYS}"
  rm -f -- "${validation_file}"
}

prepare_repository() {
  local chroot_metadata
  local repository_metadata
  local target_gid
  local target_uid

  [[ -d "${REPOSITORY_ROOT}" && ! -L "${REPOSITORY_ROOT}" ]] \
    || fail "${REPOSITORY_ROOT} must be a mounted directory"
  [[ -d /srv/portfolio-sidecar && ! -L /srv/portfolio-sidecar ]] \
    || fail "SFTP chroot must be a real directory"
  chroot_metadata="$(stat --format='%u:%g:%a' -- /srv/portfolio-sidecar)" \
    || fail "could not inspect the SFTP chroot"
  [[ "${chroot_metadata}" == "0:0:755" ]] \
    || fail "SFTP chroot must remain root-owned with mode 0755"
  target_uid="$(id -u "${TARGET_USER}")" \
    || fail "could not resolve the SFTP target user"
  target_gid="$(id -g "${TARGET_GROUP}")" \
    || fail "could not resolve the SFTP target group"
  repository_metadata="$(stat --format='%u:%g:%a' -- "${REPOSITORY_ROOT}")" \
    || fail "could not inspect the Restic repository volume"
  if [[ "${repository_metadata}" == "${target_uid}:${target_gid}:700" ]]; then
    return
  fi
  [[ "${repository_metadata}" == 0:0:* ]] \
    || fail "repository volume has unexpected ownership or permissions"
  chmod 0700 "${REPOSITORY_ROOT}"
  chown "${TARGET_USER}:${TARGET_GROUP}" "${REPOSITORY_ROOT}"
}

main() {
  local port="${SIDECAR_SFTP_PORT:-2222}"
  local repository_root="${SIDECAR_SFTP_REPOSITORY:-${REPOSITORY_ROOT}}"
  local host_key_source="${SIDECAR_SFTP_HOST_KEY:-/run/secrets/sidecar-host-key}"
  local authorized_keys_source="${SIDECAR_SFTP_AUTHORIZED_KEYS:-/run/secrets/sidecar-authorized-keys}"

  [[ "$#" -eq 0 ]] || fail "this image does not accept command arguments"
  validate_port "${port}"
  [[ "${repository_root}" == "${REPOSITORY_ROOT}" ]] \
    || fail "SIDECAR_SFTP_REPOSITORY must be ${REPOSITORY_ROOT}"
  validate_secret_source "SFTP host key" "${host_key_source}" 65536
  validate_secret_source "SFTP authorized_keys" "${authorized_keys_source}" 1048576
  [[ "${host_key_source}" != "${authorized_keys_source}" ]] \
    || fail "host-key and authorized-keys secrets must be distinct"

  prepare_runtime_root
  prepare_host_key "${host_key_source}"
  prepare_authorized_keys "${authorized_keys_source}"
  prepare_repository

  /usr/sbin/sshd -t -f /etc/ssh/sshd_config -o "Port=${port}"
  exec /usr/sbin/sshd \
    -D \
    -e \
    -f /etc/ssh/sshd_config \
    -o "Port=${port}"
}

main "$@"
