#!/usr/bin/env bash
# Thin launchd entry point for the stateful Python runtime adapter.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 "${SCRIPT_DIR}/run_traction_control_job.py" "$@"
