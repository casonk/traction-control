"""Tests for agentic_run_with_fallback in scripts/lib/agentic_provider.sh."""

from __future__ import annotations

import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agentic_provider.sh"


def _run(tmp_path: Path, body: str, *, requested: str = "auto", window: int = 120):
    """Source the library, stub the providers, and run the fallback once."""
    output = tmp_path / "agent-output.txt"
    script = f"""
set -euo pipefail
AGENTIC_FALLBACK_WINDOW_SECONDS={window}
source "{LIB}"
log() {{ printf 'LOG %s\\n' "$*"; }}
READY="codex claude copilot"
agentic_provider_ready() {{ [[ " $READY " == *" $1 "* ]]; }}
{body}
AGENT_STATUS=0
agentic_run_with_fallback "{output}" "{requested}" "" || AGENT_STATUS=$?
printf 'PROVIDER=%s STATUS=%s\\n' "$PROVIDER" "$AGENT_STATUS"
"""
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout, output.read_text(encoding="utf-8")


def test_a_successful_run_does_not_fall_back(tmp_path):
    stdout, output = _run(
        tmp_path,
        """
PROVIDER=codex
run_codex() { echo codex-did-it; }
run_claude() { echo claude-should-not-run; }
""",
    )
    assert "PROVIDER=codex STATUS=0" in stdout
    assert output == "codex-did-it\n"


def test_an_early_failure_falls_back_to_the_next_ready_provider(tmp_path):
    stdout, output = _run(
        tmp_path,
        """
PROVIDER=codex
run_codex() { echo "error: unexpected argument '--full-auto' found"; return 2; }
run_claude() { echo claude-did-it; }
""",
    )
    assert "PROVIDER=claude STATUS=0" in stdout
    assert "unexpected argument" in output
    assert "falling back to claude" in output
    assert output.endswith("claude-did-it\n")


def test_providers_that_are_not_ready_are_skipped(tmp_path):
    stdout, _ = _run(
        tmp_path,
        """
READY="codex copilot"
PROVIDER=codex
run_codex() { return 2; }
run_claude() { echo claude-should-not-run; }
run_copilot() { echo copilot-did-it; }
""",
    )
    assert "PROVIDER=copilot STATUS=0" in stdout


def test_the_last_failure_is_returned_when_every_provider_fails(tmp_path):
    stdout, _ = _run(
        tmp_path,
        """
PROVIDER=codex
run_codex() { return 2; }
run_claude() { return 3; }
run_copilot() { return 4; }
""",
    )
    assert "PROVIDER=copilot STATUS=4" in stdout


def test_an_explicitly_requested_provider_is_never_replaced(tmp_path):
    stdout, _ = _run(
        tmp_path,
        """
PROVIDER=codex
run_codex() { return 2; }
run_claude() { echo claude-should-not-run; }
""",
        requested="codex",
    )
    assert "PROVIDER=codex STATUS=2" in stdout


def test_a_late_failure_does_not_fall_back(tmp_path):
    # A zero-second window makes every failure "late": the agent may have
    # started work, so another agent must not pick up a half-changed tree.
    stdout, _ = _run(
        tmp_path,
        """
PROVIDER=codex
run_codex() { return 1; }
run_claude() { echo claude-should-not-run; }
""",
        window=0,
    )
    assert "PROVIDER=codex STATUS=1" in stdout
    assert "not falling back" in stdout


def test_a_capacity_failure_falls_back_even_when_late(tmp_path):
    stdout, _ = _run(
        tmp_path,
        """
PROVIDER=codex
run_codex() { echo "usage limit reached"; return 1; }
run_claude() { echo claude-did-it; }
""",
        window=0,
    )
    assert "PROVIDER=claude STATUS=0" in stdout
