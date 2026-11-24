#!/usr/bin/env bash
# Weekly resource-limit evidence.
#
# Prints per-job peaks, then runs the regulator with the user journal so a job
# within 15% of its memory ceiling is reported before it is killed. This does
# not raise any limit: raising still needs `resource_limits.py raise` with a
# reason and evidence. The daily portfolio audit runs `check --no-journal`
# because a 30-day user journal is too slow for that pass.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 "${ROOT}/scripts/resource_limits.py" observe
observe_status=$?
python3 "${ROOT}/scripts/resource_limits.py" check
check_status=$?

if (( observe_status != 0 )); then
  exit "${observe_status}"
fi
exit "${check_status}"
