"""Tests for the busy-checkout pre-check and the local provider in
scripts/lib/agentic_provider.sh."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agentic_provider.sh"
HOUR_AGO = time.time() - 3600


def _bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nsource "{LIB}"\n{script}'],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("build/\n")
    (repo / "tracked.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    for path in [*repo.rglob("*")]:
        if path.is_file():
            os.utime(path, (HOUR_AGO, HOUR_AGO))
    return repo


def _busy(repo: Path, **env: str) -> subprocess.CompletedProcess:
    return _bash(
        f'agentic_checkout_busy "{repo}"',
        env={"AGENTIC_SKIP_PROCESS_CHECK": "1", **env},
    )


def test_a_checkout_untouched_for_the_quiet_window_is_not_busy(tmp_path):
    assert _busy(_repo(tmp_path)).returncode == 1


def test_a_recent_edit_makes_the_checkout_busy(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.txt").write_text("edited\n")
    result = _busy(repo)
    assert result.returncode == 0
    assert "tracked.txt changed" in result.stdout


def test_a_new_untracked_file_counts_but_an_ignored_one_does_not(tmp_path):
    repo = _repo(tmp_path)
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_text("generated\n")
    assert _busy(repo).returncode == 1
    (repo / "notes.md").write_text("new\n")
    assert _busy(repo).returncode == 0


def test_a_zero_quiet_window_disables_the_mtime_check(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.txt").write_text("edited\n")
    assert _busy(repo, AGENTIC_QUIET_SECONDS="0").returncode == 1


def test_an_agent_process_working_in_the_checkout_makes_it_busy(tmp_path):
    repo = _repo(tmp_path)
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text("#!/bin/bash\nsleep 30\ntrue\n")
    fake.chmod(0o755)
    agent = subprocess.Popen([str(fake)], cwd=repo / ".git")
    try:
        time.sleep(0.3)
        result = _bash(f'agentic_checkout_busy "{repo}"')
    finally:
        agent.kill()
        agent.wait()
    assert result.returncode == 0
    assert f"agent process {agent.pid} (codex)" in result.stdout


def test_defer_exits_cleanly_and_logs_the_reason(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.txt").write_text("edited\n")
    result = _bash(
        f'log() {{ printf "LOG %s\\n" "$*"; }}\nagentic_defer_if_busy "{repo}"\necho not-reached',
        env={"AGENTIC_SKIP_PROCESS_CHECK": "1"},
    )
    assert result.returncode == 0
    assert "deferred" in result.stdout
    assert "not-reached" not in result.stdout
    forced = _bash(
        f'FORCE=1\nagentic_defer_if_busy "{repo}"\necho reached',
        env={"AGENTIC_SKIP_PROCESS_CHECK": "1"},
    )
    assert "reached" in forced.stdout


def test_the_local_provider_is_off_unless_asked_for():
    result = _bash("agentic_local_status", env={"AGENTIC_LOCAL_FALLBACK": "0"})
    assert result.returncode == 3
    assert "AGENTIC_LOCAL_FALLBACK=1" in result.stdout


def test_the_local_run_reuses_run_codex_with_the_ollama_flags(tmp_path):
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text('#!/bin/bash\nprintf "ARGS %s\\n" "$*"\n')
    fake.chmod(0o755)
    result = _bash(
        'run_codex() { local cmd=(codex exec --sandbox workspace-write); '
        'cmd+=(--model "${MODEL_REQUESTED}"); "${cmd[@]}" -; }\n'
        "MODEL_REQUESTED=gpt-x\nagentic_run_local\n"
        'echo "after=${MODEL_REQUESTED} codex=$(type -t codex)"',
        env={"PATH": f"{fake.parent}:{os.environ['PATH']}", "AGENTIC_LOCAL_MODEL": "qwen3:32b"},
    )
    assert result.returncode == 0, result.stderr
    assert (
        "ARGS exec --oss --local-provider ollama --sandbox workspace-write --model qwen3:32b -"
        in result.stdout
    )
    # The model override and the codex shadow end with the call.
    assert "after=gpt-x codex=file" in result.stdout


def test_auto_falls_back_to_local_after_the_hosted_providers(tmp_path):
    output = tmp_path / "out.txt"
    result = _bash(
        'log() { :; }\nagentic_provider_ready() { [[ "$1" == local ]]; }\n'
        "PROVIDER=codex\n"
        'run_codex() { [[ "$(type -t codex)" == function ]] && { echo local-did-it; return 0; }; '
        'echo "quota exceeded"; return 1; }\n'
        f'agentic_run_with_fallback "{output}" auto ""\necho "PROVIDER=$PROVIDER"',
    )
    assert result.returncode == 0, result.stderr
    assert "PROVIDER=local" in result.stdout
    assert output.read_text().endswith("local-did-it\n")


def test_git_status_rewriting_the_index_does_not_make_a_checkout_busy(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True)
    assert _busy(repo).returncode == 1


def test_a_recent_commit_makes_the_checkout_busy(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-qm", "init"],
        check=True,
    )
    for path in (repo / "tracked.txt", repo / ".gitignore"):
        os.utime(path, (HOUR_AGO, HOUR_AGO))
    result = _busy(repo)
    assert result.returncode == 0
    assert "git HEAD reflog" in result.stdout
