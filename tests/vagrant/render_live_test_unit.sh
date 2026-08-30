#!/usr/bin/env bash
# Render one inert timer/service pair for the live systemd activation fixture.
set -euo pipefail

job_name="$1"
shift
unit_dir=""
while (( $# > 0 )); do
  case "$1" in
    --unit-dir|--portfolio-root|--clockwork-repo)
      [[ $# -ge 2 ]] || { printf 'missing value for %s\n' "$1" >&2; exit 2; }
      if [[ "$1" == "--unit-dir" ]]; then
        unit_dir="$2"
      fi
      shift 2
      ;;
    --render-only) shift ;;
    *) printf 'unexpected fixture argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -n "${unit_dir}" ]] || { printf '%s\n' 'missing --unit-dir' >&2; exit 2; }
mkdir -p "${unit_dir}"
cat > "${unit_dir}/${job_name}.service" <<EOF
[Service]
Type=oneshot
ExecStart=/usr/bin/false
EOF
cat > "${unit_dir}/${job_name}.timer" <<EOF
[Timer]
Unit=${job_name}.service
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
EOF
