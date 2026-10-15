"""Tests for scripts/realign_rewritten_checkouts.sh against a simulated rewrite."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "realign_rewritten_checkouts.sh"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], env=ENV, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-qm", message)


def long_history(repo: Path, commits: int) -> None:
    """Add `commits` commits quickly with fast-import, each with its own tree."""
    stream = []
    for n in range(commits):
        data = f"{n}\n"
        stream.append(
            f"commit refs/heads/main\ncommitter t <t@example.invalid> {1_700_000_000 + n} +0000\n"
            f"data 5\nbase\nM 644 inline base.txt\ndata {len(data)}\n{data}\n"
        )
    subprocess.run(
        ["git", "-C", str(repo), "fast-import", "--quiet"],
        input="".join(stream),
        text=True,
        env=ENV,
        check=True,
    )
    git(repo, "reset", "-q", "--hard", "main")


def portfolio(
    tmp_path: Path, name: str = "repo", *, history: int = 0
) -> tuple[Path, Path, Path]:
    """Origin, a writer clone that rewrites it, and the stale clone under test."""
    origin = tmp_path / "origin" / f"{name}.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    writer = tmp_path / "writer" / name
    git(tmp_path, "clone", "-q", str(origin), str(writer))
    if history:
        long_history(writer, history)
    commit(writer, "a.txt", "one\n", "feat: one\n\nCo-Authored-By: Tool <t@example.invalid>")
    commit(writer, "b.txt", "two\n", "feat: two")
    git(writer, "push", "-q", "origin", "main")
    stale = tmp_path / "root" / "util-repos" / name
    git(tmp_path, "clone", "-q", str(origin), str(stale))
    # Rewrite: same trees, new messages, force-pushed; then one more commit.
    # With a long base history, rewrite only the top two commits: rebasing
    # thousands one at a time is slow, and the matching tree stays near the
    # top of `git log`, which is the case that matters.
    base = "HEAD~2" if history else "--root"
    git(writer, "rebase", "-q", base, "--exec", "git commit -q --amend -m rewritten")
    commit(writer, "c.txt", "three\n", "feat: three")
    git(writer, "push", "-q", "--force", "origin", "main")
    return origin, writer, stale


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--root", str(root), "--tag", "T", *args],
        env=ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dry_run_reports_and_changes_nothing(tmp_path):
    _, _, stale = portfolio(tmp_path)
    before = git(stale, "rev-parse", "HEAD")

    result = run(tmp_path / "root")

    assert result.returncode == 0, result.stderr
    assert "would keep legacy/pre-rewrite-T" in result.stdout
    assert git(stale, "rev-parse", "HEAD") == before


def test_apply_moves_a_clean_clone_and_keeps_a_backup_branch(tmp_path):
    _, writer, stale = portfolio(tmp_path)
    before = git(stale, "rev-parse", "HEAD")

    result = run(tmp_path / "root", "--apply")

    assert result.returncode == 0, result.stderr
    assert git(stale, "rev-parse", "HEAD") == git(writer, "rev-parse", "HEAD")
    assert git(stale, "rev-parse", "legacy/pre-rewrite-T") == before
    assert (stale / "c.txt").read_text() == "three\n"


def test_apply_keeps_unrelated_local_changes(tmp_path):
    _, writer, stale = portfolio(tmp_path)
    (stale / "a.txt").write_text("local edit\n")
    (stale / "notes.txt").write_text("untracked\n")

    result = run(tmp_path / "root", "--apply")

    assert result.returncode == 0, result.stderr
    assert git(stale, "rev-parse", "HEAD") == git(writer, "rev-parse", "HEAD")
    assert (stale / "a.txt").read_text() == "local edit\n"
    assert (stale / "notes.txt").read_text() == "untracked\n"
    assert (stale / "c.txt").read_text() == "three\n"
    assert [line.strip() for line in git(stale, "status", "--porcelain").splitlines()] == [
        "M a.txt",
        "?? notes.txt",
    ]


def test_local_changes_overlapping_origin_changes_are_left_alone(tmp_path):
    _, _, stale = portfolio(tmp_path)
    (stale / "c.txt").write_text("would be overwritten\n")
    before = git(stale, "rev-parse", "HEAD")

    result = run(tmp_path / "root", "--apply")

    assert "local changes touch files origin changed" in result.stdout
    assert git(stale, "rev-parse", "HEAD") == before
    assert (stale / "c.txt").read_text() == "would be overwritten\n"


def test_local_only_commits_are_never_reset(tmp_path):
    _, _, stale = portfolio(tmp_path)
    commit(stale, "mine.txt", "work\n", "feat: unpushed work")
    before = git(stale, "rev-parse", "HEAD")

    result = run(tmp_path / "root", "--apply")

    assert "diverged with local-only commits" in result.stdout
    assert git(stale, "rev-parse", "HEAD") == before


def test_other_branches_are_left_alone(tmp_path):
    _, _, stale = portfolio(tmp_path)
    git(stale, "switch", "-q", "-c", "topic")

    result = run(tmp_path / "root", "--apply")

    assert "on 'topic'" in result.stdout
    assert git(stale, "branch", "--show-current") == "topic"


def test_a_clone_that_is_only_behind_is_fast_forwarded(tmp_path):
    origin = tmp_path / "origin" / "plain.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    writer = tmp_path / "writer" / "plain"
    git(tmp_path, "clone", "-q", str(origin), str(writer))
    commit(writer, "a.txt", "one\n", "feat: one")
    git(writer, "push", "-q", "origin", "main")
    stale = tmp_path / "root" / "plain"
    git(tmp_path, "clone", "-q", str(origin), str(stale))
    commit(writer, "b.txt", "two\n", "feat: two")
    git(writer, "push", "-q", "origin", "main")

    result = run(tmp_path / "root", "--apply")

    assert "fast-forwarded" in result.stdout
    assert git(stale, "rev-parse", "HEAD") == git(writer, "rev-parse", "HEAD")
    assert "legacy/pre-rewrite-T" not in git(stale, "branch")


def test_a_long_history_is_still_recognised_as_rewritten(tmp_path):
    # Thousands of tree lines: grep finds the match while git log is still
    # writing. With `grep -q` under pipefail that SIGPIPE read as "not found"
    # and every real repository was skipped as having local-only commits.
    _, writer, stale = portfolio(tmp_path, history=3000)

    result = run(tmp_path / "root", "--apply")

    assert "moved to origin" in result.stdout, result.stdout
    assert git(stale, "rev-parse", "HEAD") == git(writer, "rev-parse", "HEAD")
