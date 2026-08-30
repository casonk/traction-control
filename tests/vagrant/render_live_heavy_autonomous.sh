#!/usr/bin/env bash
exec "$(dirname "${BASH_SOURCE[0]}")/render_live_test_unit.sh" ci-repair-agentic "$@"
