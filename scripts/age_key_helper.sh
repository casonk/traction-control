#!/usr/bin/env bash
# age_key_helper.sh — keep age identities recoverable without ever printing a secret key.
#
#   recovery-key        generate a NEW recovery key pair, passphrase-protect the secret half, and write the public
#                       recipient beside it. Run it yourself in a terminal (age prompts for the passphrase on the TTY).
#   protect FILE        passphrase-protect an existing identity file into the key directory.
#
# Both write only below --dir (default /mnt/16tb-sata/portfolio-backups/keys), which must be on a spinning disk and outside
# every Git worktree. Nothing is overwritten. The plaintext secret exists only in a private temporary file that is shredded.
# A passphrase-protected copy on this machine's disk is a convenience, not offline storage: also copy the .age file to
# removable media kept elsewhere. Do not store the passphrase only in a vault that these backups are needed to restore.
set -euo pipefail

DIR="${AGE_KEY_DIR:-/mnt/16tb-sata/portfolio-backups/keys}"
ALLOW_FLASH=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

cmd="${1:-}"; [[ -n "${cmd}" ]] || { usage; exit 2; }; shift || true
FILE=""
if [[ "${cmd}" == "protect" ]]; then FILE="${1:-}"; [[ -n "${FILE}" ]] || fail "protect needs a FILE"; shift; fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --allow-flash) ALLOW_FLASH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
case "${cmd}" in recovery-key|protect) ;; -h|--help) usage; exit 0 ;; *) fail "unknown command: ${cmd}" ;; esac

command -v age >/dev/null 2>&1 || fail "age not found"
command -v age-keygen >/dev/null 2>&1 || fail "age-keygen not found"

kind="$(python3 -I "${SCRIPT_DIR}/check_bulk_storage.py" --kind "${DIR}")"
if [[ "${kind}" != "hdd" && ${ALLOW_FLASH} -eq 0 ]]; then
  fail "${DIR} is on ${kind} storage; keep key backups on a spinning disk, or pass --allow-flash"
fi
if git -C "$(dirname "${DIR}")" rev-parse --git-dir >/dev/null 2>&1 || git -C "${DIR}" rev-parse --git-dir >/dev/null 2>&1; then
  fail "${DIR} is inside a Git worktree; key backups must live outside every repository"
fi
umask 077
mkdir -p "${DIR}"; chmod 700 "${DIR}"

TMP=""
cleanup() { if [[ -n "${TMP}" && -e "${TMP}" ]]; then shred -u "${TMP}" 2>/dev/null || rm -f "${TMP}"; fi; }
trap cleanup EXIT
tmpdir="${XDG_RUNTIME_DIR:-/dev/shm}"; [[ -d "${tmpdir}" && -w "${tmpdir}" ]] || tmpdir="$(mktemp -d)"

case "${cmd}" in
  recovery-key)
    out="${DIR}/recovery-key.age"; pub="${DIR}/recovery-recipient.pub"
    [[ ! -e "${out}" && ! -e "${pub}" ]] || fail "refusing to overwrite ${out} or ${pub}"
    TMP="$(mktemp "${tmpdir}/age-recovery.XXXXXX")"
    age-keygen -o "${TMP}" 2> "${TMP}.pub" || { rm -f "${TMP}.pub"; fail "age-keygen failed"; }
    recipient="$(sed -n 's/^Public key: //p' "${TMP}.pub")"; rm -f "${TMP}.pub"
    [[ "${recipient}" == age1* ]] || fail "could not read the public key"
    age -p -o "${out}" "${TMP}"
    printf '%s\n' "${recipient}" > "${pub}"
    ;;
  protect)
    [[ -f "${FILE}" ]] || fail "no such file: ${FILE}"
    out="${DIR}/$(basename "${FILE}").passphrase"
    [[ ! -e "${out}" ]] || fail "refusing to overwrite ${out}"
    age -p -o "${out}" "${FILE}"
    ;;
esac

chmod 600 "${out}"
head -c 21 "${out}" | grep -q '^age-encryption.org/v1' || { rm -f "${out}"; fail "output is not an age file"; }
printf 'wrote %s (passphrase-protected)\n' "${out}"
if [[ "${cmd}" == "recovery-key" ]]; then
  printf 'public recipient (safe to share; add it to the backup recipients): %s\n' "$(cat "${pub}")"
  printf 'copy %s to removable media kept away from this machine.\n' "${out}"
fi
