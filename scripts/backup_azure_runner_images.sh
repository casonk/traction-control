#!/usr/bin/env bash
# backup_azure_runner_images.sh — keep a local copy of the golden runner images.
#
#   backup_azure_runner_images.sh [--dry-run] [--only windows|linux] [--dest DIR]
#
# The images live only in the Azure compute gallery, in one region and one replica, and the subscription runs on
# credits, so a copy that survives the resource group is worth having. For each image this script creates a temporary
# managed disk from the recorded image version, downloads it through a short-lived read-only link (never printed or put
# on a command line), checks it is a complete fixed VHD, records its SHA-256, compresses it with zstd, proves the
# compressed file decompresses to the same hash, then revokes the link and deletes the disk. Nothing running is touched.
#
# Output (owner-only) under DIR (default ~/.local/share/portfolio-backups/runner-images):
#   <image>-<version>.vhd.zst      the disk, compressed
#   <image>-<version>.sha256       SHA-256 of the raw VHD (what `zstd -dc FILE | sha256sum` must print)
#   <image>-<version>.manifest.txt what was baked into it (from the bake), when recorded
#
# Egress: a Windows disk is 128 GiB and a Linux disk 30 GiB; the first 100 GB a month leave Azure free and the rest costs
# about $0.09 a GB. Restore notes are in docs/self-hosted-github-runners.md. The Windows image can only be restored
# into Azure (its licence is Azure's).
set -euo pipefail

RESOURCE_GROUP="rg-win-runner"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-windows-runner"
DEST="${HOME}/.local/share/portfolio-backups/runner-images"
ONLY=""
DRY_RUN=0
SAS_SECONDS=21600

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --only) [[ $# -ge 2 ]] || fail "--only needs windows or linux"; ONLY="$2"; shift 2 ;;
    --dest) [[ $# -ge 2 ]] || fail "--dest needs a directory"; DEST="$2"; shift 2 ;;
    --help|-h) sed -n 2,21p "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ -z "$ONLY" || "$ONLY" == "windows" || "$ONLY" == "linux" ]] || fail "--only must be windows or linux"
for c in az curl zstd sha256sum stat; do command -v "$c" >/dev/null 2>&1 || fail "required command not found: $c"; done

# name | gallery definition | recorded id file | OS | extra disk flags
IMAGES=(
  "windows|windows-runner|image-version.txt|Windows|"
  "linux|linux-runner|linux-image-version.txt|Linux|--security-type TrustedLaunch"
)

WORK=""
DISK=""
cleanup() {
  if [[ -n "$DISK" ]]; then
    az disk revoke-access -g "$RESOURCE_GROUP" -n "$DISK" --only-show-errors >/dev/null 2>&1 || true
    az disk delete -g "$RESOURCE_GROUP" -n "$DISK" --yes --only-show-errors >/dev/null 2>&1 || true
  fi
  if [[ -n "$WORK" ]]; then rm -rf "$WORK"; fi
}
trap cleanup EXIT

# Download in byte ranges. Azure's disk-export endpoint drops a long connection every half minute or so, and curl's own
# retry restarts from zero, so one big request never finishes. Each range is retried on its own and appended only when it
# arrived whole, so a drop costs at most one chunk and an interrupted run resumes from the file's size.
download_ranges() {  # config-file output-file expected-bytes
  local cfg="$1" out="$2" want="$3" chunk=$((256 * 1024 * 1024)) total off end got need tries next_report
  total="$(curl -sS --fail --retry 5 --retry-all-errors -I -K "$cfg" | tr -d '\r' | awk 'tolower($1)=="content-length:"{print $2}')"
  [[ "$total" =~ ^[0-9]+$ ]] || fail "could not read the size of the export"
  if [[ "$total" != "$want" && "$total" != "$((want + 512))" ]]; then
    fail "the export is $total bytes; expected $want (a fixed VHD may add a 512-byte footer)"
  fi
  [[ -f "$out" ]] || : > "$out"
  off="$(stat -c %s "$out")"
  (( off <= total )) || fail "the partial file is larger than the export; delete $out and retry"
  next_report=$(( (off / 1073741824 + 1) * 1073741824 ))
  while (( off < total )); do
    end=$(( off + chunk - 1 ))
    (( end < total )) || end=$(( total - 1 ))
    need=$(( end - off + 1 ))
    tries=0
    while :; do
      if curl -sS --fail --connect-timeout 30 --max-time 900 -K "$cfg" -r "$off-$end" -o "$WORK/chunk" 2>/dev/null; then
        got="$(stat -c %s "$WORK/chunk")"
        [[ "$got" == "$need" ]] && break
      fi
      tries=$(( tries + 1 ))
      (( tries < 15 )) || fail "the range starting at byte $off failed 15 times in a row"
      sleep $(( tries < 6 ? tries * 4 : 24 ))
    done
    cat "$WORK/chunk" >> "$out"
    off=$(( off + need ))
    if (( off >= next_report )); then
      printf '   %s: %s of %s GiB\n' "$(basename "$out" | cut -c2-)" "$((off / 1073741824))" "$((total / 1073741824))"
      next_report=$(( next_report + 4 * 1073741824 ))
    fi
  done
  rm -f "$WORK/chunk"
}

backup_one() {
  local name="$1" def="$2" idfile="$3" os="$4" extra="$5"
  [[ -f "$STATE_DIR/$idfile" ]] || fail "no $name image is recorded in $STATE_DIR/$idfile"
  local image_id version base
  image_id="$(tr -d '[:space:]' < "$STATE_DIR/$idfile")"
  [[ "$image_id" == /subscriptions/* ]] || fail "$idfile does not hold an Azure image version id"
  version="${image_id##*/}"
  base="$DEST/$def-$version"

  if [[ -f "$base.vhd.zst" && -f "$base.sha256" ]]; then
    printf '== %s %s: a backup exists; checking it\n' "$name" "$version"
    if [[ "$(zstd -dc "$base.vhd.zst" | sha256sum | cut -d' ' -f1)" == "$(cut -d' ' -f1 "$base.sha256")" ]]; then
      printf '   ok: %s already holds an intact copy (%s)\n' "$base.vhd.zst" "$(du -h "$base.vhd.zst" | cut -f1)"
      return 0
    fi
    printf '   the existing copy does not match its hash; replacing it\n'
    rm -f "$base.vhd.zst" "$base.sha256"
  fi

  printf '== %s %s: creating a temporary disk from the image\n' "$name" "$version"
  DISK="bk-$def"
  # shellcheck disable=SC2086
  az disk create -g "$RESOURCE_GROUP" -n "$DISK" --gallery-image-reference "$image_id" --os-type "$os" \
    --sku Standard_LRS --hyper-v-generation V2 $extra --tags purpose=image-backup --only-show-errors -o none
  local bytes
  bytes="$(az disk show -g "$RESOURCE_GROUP" -n "$DISK" --query diskSizeBytes -o tsv --only-show-errors)"
  [[ "$bytes" =~ ^[0-9]+$ ]] || fail "could not read the disk size"

  printf '== %s: opening a read-only link (not shown) and downloading %s GiB\n' "$name" "$((bytes / 1073741824))"
  local sas cfg raw
  sas="$(az disk grant-access -g "$RESOURCE_GROUP" -n "$DISK" --access-level Read --duration-in-seconds "$SAS_SECONDS" \
    --query "accessSAS || accessSas" -o tsv --only-show-errors)"
  [[ "$sas" == https://* ]] || fail "no download link was issued"
  raw="$DEST/.$def-$version.vhd.partial"
  cfg="$WORK/$def.curl"
  (umask 077; printf 'url = "%s"\n' "$sas" > "$cfg")
  unset sas
  download_ranges "$cfg" "$raw" "$bytes"
  rm -f "$cfg"

  local size
  size="$(stat -c %s "$raw")"
  if [[ "$size" != "$bytes" && "$size" != "$((bytes + 512))" ]]; then
    fail "the download is $size bytes; expected $bytes (a fixed VHD may add a 512-byte footer)"
  fi
  if [[ "$(tail -c 512 "$raw" | head -c 8)" != "conectix" ]]; then
    fail "the file does not end with a VHD footer; it is incomplete"
  fi
  printf '== %s: size and VHD footer ok; hashing\n' "$name"
  local sum
  sum="$(sha256sum "$raw" | cut -d' ' -f1)"
  (umask 077; printf '%s  %s\n' "$sum" "$def-$version.vhd" > "$base.sha256")

  printf '== %s: compressing\n' "$name"
  (umask 077; zstd -T0 -3 -q -f "$raw" -o "$base.vhd.zst")
  printf '== %s: proving the compressed copy restores to the same hash\n' "$name"
  if [[ "$(zstd -dc "$base.vhd.zst" | sha256sum | cut -d' ' -f1)" != "$sum" ]]; then
    rm -f "$base.vhd.zst" "$base.sha256"
    fail "the compressed copy does not decompress to the downloaded disk; discarded"
  fi
  rm -f "$raw"

  local m="$STATE_DIR/manifests/$name-$version.txt"
  if [[ -f "$m" ]]; then (umask 077; cp "$m" "$base.manifest.txt"); fi
  chmod 600 "$base".* 2>/dev/null || true

  az disk revoke-access -g "$RESOURCE_GROUP" -n "$DISK" --only-show-errors >/dev/null 2>&1 || true
  az disk delete -g "$RESOURCE_GROUP" -n "$DISK" --yes --only-show-errors >/dev/null 2>&1 || true
  DISK=""
  printf '== %s: done: %s (%s, sha256 %s...)\n' "$name" "$base.vhd.zst" "$(du -h "$base.vhd.zst" | cut -f1)" "${sum:0:12}"
}

printf 'Destination: %s\n' "$DEST"
for entry in "${IMAGES[@]}"; do
  IFS='|' read -r name def idfile os extra <<< "$entry"
  if [[ -n "$ONLY" && "$ONLY" != "$name" ]]; then continue; fi
  printf 'Image      : %s (%s) recorded in %s\n' "$name" "$def" "$idfile"
done
if (( DRY_RUN == 1 )); then printf 'dry run: nothing was changed.\n'; exit 0; fi

printf 'This creates temporary disks, downloads about 158 GiB in total and deletes the disks. Type BACKUP to continue: '
read -r answer
[[ "$answer" == "BACKUP" ]] || fail "operation cancelled"

install -d -m 700 "$(dirname "$DEST")" "$DEST"
WORK="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/image-backup.XXXXXX")"
chmod 700 "$WORK"
for entry in "${IMAGES[@]}"; do
  IFS='|' read -r name def idfile os extra <<< "$entry"
  if [[ -n "$ONLY" && "$ONLY" != "$name" ]]; then continue; fi
  backup_one "$name" "$def" "$idfile" "$os" "$extra"
done
printf 'All requested backups are in %s\n' "$DEST"
