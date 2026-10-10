"""Real-Git tests for portfolio_purge: what it removes, and what it must not."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import portfolio_purge as purge  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def sh(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class PortfolioPurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = dict(os.environ)
        os.environ.update(GIT_ENV)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        self.origin = root / "origin.git"
        self.repo = root / "portfolio" / "group" / "repo"
        self.repo.parent.mkdir(parents=True)
        sh(root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.origin))
        sh(root, "clone", "--quiet", str(self.origin), str(self.repo))
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        sh(self.repo, "add", "README.md")
        sh(self.repo, "commit", "--quiet", "-m", "base")
        sh(self.repo, "push", "--quiet", "-u", "origin", "HEAD:main")
        sh(self.repo, "checkout", "--quiet", "-B", "main", "origin/main")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        self.temporary.cleanup()

    # -- helpers ---------------------------------------------------------

    def branches(self) -> set[str]:
        return set(sh(self.repo, "branch", "--format=%(refname:short)").splitlines())

    def worktrees(self) -> set[str]:
        lines = sh(self.repo, "worktree", "list", "--porcelain").splitlines()
        return {Path(x.split(" ", 1)[1]).name for x in lines if x.startswith("worktree ")}

    def squash_merged_branch(self, name: str) -> None:
        """A branch whose change reached main as a different (squash) commit."""
        sh(self.repo, "checkout", "--quiet", "-b", name)
        (self.repo / f"{name}.txt").write_text("feature\n", encoding="utf-8")
        sh(self.repo, "add", ".")
        sh(self.repo, "commit", "--quiet", "-m", f"work on {name}")
        sh(self.repo, "checkout", "--quiet", "main")
        sh(self.repo, "merge", "--quiet", "--squash", name)
        sh(self.repo, "commit", "--quiet", "-m", f"{name} (squashed)")
        sh(self.repo, "push", "--quiet", "origin", "main")

    def unmerged_branch(self, name: str) -> None:
        sh(self.repo, "checkout", "--quiet", "-b", name)
        (self.repo / f"{name}.txt").write_text("not on main\n", encoding="utf-8")
        sh(self.repo, "add", ".")
        sh(self.repo, "commit", "--quiet", "-m", f"work on {name}")
        sh(self.repo, "checkout", "--quiet", "main")

    def add_worktree(self, name: str, branch: str) -> Path:
        path = self.repo / ".claude" / "worktrees" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        sh(self.repo, "worktree", "add", "--quiet", str(path), branch)
        return path

    def run_purge(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = purge.main(["--repo", str(self.repo), "--no-gh", *args])
        return code, out.getvalue()

    # -- branches --------------------------------------------------------

    def test_dry_run_reports_but_changes_nothing(self) -> None:
        self.squash_merged_branch("done")
        code, output = self.run_purge()
        self.assertEqual(code, 0)
        self.assertIn("would remove: repo: branch done", output)
        self.assertIn("dry run", output)
        self.assertIn("done", self.branches())

    def test_squash_merged_branch_is_deleted(self) -> None:
        # `git branch -d` refuses this branch; it is the common case.
        self.squash_merged_branch("done")
        code, _ = self.run_purge("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.branches(), {"main"})

    def test_branch_with_unmerged_work_is_kept(self) -> None:
        self.unmerged_branch("wip")
        code, output = self.run_purge("--apply", "--verbose")
        self.assertEqual(code, 0)
        self.assertIn("wip", self.branches())
        self.assertIn("kept: repo: branch wip (needs-pr", output)

    def test_checked_out_branch_is_never_deleted(self) -> None:
        self.squash_merged_branch("done")
        sh(self.repo, "checkout", "--quiet", "done")
        self.run_purge("--apply")
        self.assertIn("done", self.branches())

    # -- worktrees -------------------------------------------------------

    def test_old_clean_merged_worktree_is_removed_with_its_branch(self) -> None:
        self.squash_merged_branch("done")
        path = self.add_worktree("old", "done")
        code, _ = self.run_purge("--apply", "--min-age-days", "0")
        self.assertEqual(code, 0)
        self.assertFalse(path.exists())
        self.assertEqual(self.branches(), {"main"})

    def test_recent_worktree_is_kept_by_the_age_guard(self) -> None:
        # Clean and "merged", but created seconds ago: someone's work starting.
        self.squash_merged_branch("done")
        path = self.add_worktree("fresh", "done")
        _, output = self.run_purge("--apply", "--verbose")
        self.assertTrue(path.exists())
        self.assertIn("newer than 3 days", output)
        self.assertIn("done", self.branches())

    def test_inspecting_a_worktree_does_not_touch_its_index(self) -> None:
        # A plain `git status` rewrites the index. If the purge did that, every
        # run would reset the evidence of how old a worktree is.
        self.squash_merged_branch("done")
        path = self.add_worktree("old", "done")
        index = Path(sh(path, "rev-parse", "--absolute-git-dir")) / "index"
        os.utime(index, (1_000_000_000, 1_000_000_000))
        self.run_purge("--verbose")
        self.assertEqual(index.stat().st_mtime, 1_000_000_000)

    def test_dirty_worktree_is_kept(self) -> None:
        self.squash_merged_branch("done")
        path = self.add_worktree("dirty", "done")
        (path / "scratch.txt").write_text("unsaved\n", encoding="utf-8")
        _, output = self.run_purge("--apply", "--min-age-days", "0", "--verbose")
        self.assertTrue(path.exists())
        self.assertIn("uncommitted changes", output)
        self.assertIn("done", self.branches())

    def test_locked_worktree_is_kept(self) -> None:
        self.squash_merged_branch("done")
        path = self.add_worktree("held", "done")
        sh(self.repo, "worktree", "lock", str(path))
        _, output = self.run_purge("--apply", "--min-age-days", "0", "--verbose")
        self.assertTrue(path.exists())
        self.assertIn("(locked)", output)

    def test_worktree_on_unmerged_branch_is_kept(self) -> None:
        self.unmerged_branch("wip")
        path = self.add_worktree("active", "wip")
        self.run_purge("--apply", "--min-age-days", "0")
        self.assertTrue(path.exists())
        self.assertIn("wip", self.branches())
        self.assertIn("active", self.worktrees())

    # -- remote branches -------------------------------------------------

    def test_remote_branches_are_untouched_unless_requested(self) -> None:
        sh(self.repo, "push", "--quiet", "origin", "main:refs/heads/leftover")
        sh(self.repo, "fetch", "--quiet", "origin")
        self.run_purge("--apply")
        self.assertIn("leftover", sh(self.repo, "ls-remote", "--heads", "origin"))

    def test_remote_branch_with_nothing_new_is_deleted_on_request(self) -> None:
        sh(self.repo, "push", "--quiet", "origin", "main:refs/heads/leftover")
        self.unmerged_branch("wip")
        sh(self.repo, "push", "--quiet", "origin", "wip")
        sh(self.repo, "fetch", "--quiet", "origin")
        code, _ = self.run_purge("--apply", "--remote-branches")
        self.assertEqual(code, 0)
        heads = sh(self.repo, "ls-remote", "--heads", "origin")
        self.assertNotIn("leftover", heads)
        self.assertIn("refs/heads/wip", heads)
        self.assertIn("refs/heads/main", heads)


class PortfolioPurgeManifestTests(unittest.TestCase):
    """The scheduled job must point at real files and survive launchd's PATH."""

    REPO_ROOT = Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
            self.skipTest("tomllib is unavailable")
        manifest = self.REPO_ROOT / "config" / "clockwork" / "portfolio-purge.toml"
        (self.job,) = tomllib.loads(manifest.read_text(encoding="utf-8"))["jobs"]

    def test_exec_start_names_an_executable_script(self) -> None:
        interpreter, script = self.job["exec_start"].split()
        self.assertEqual(interpreter, "/bin/bash")
        target = self.REPO_ROOT / script
        self.assertTrue(target.is_file(), target)
        self.assertTrue(os.access(target, os.X_OK), f"{target} is not executable")

    def test_schedule_is_weekly_and_cron_agrees_with_the_timer(self) -> None:
        self.assertEqual(self.job["timer"]["on_calendar"], "Sun *-*-* 04:30:00")
        self.assertEqual(self.job["cron"]["expression"], "30 4 * * 0")

    def test_launchd_path_reaches_homebrew(self) -> None:
        # launchd's default PATH has no Homebrew, so gh would not be found and
        # the purge would quietly stop recognising squash-merged branches.
        path = self.job["launchd"]["environment"]["PATH"].split(":")
        self.assertIn("/opt/homebrew/bin", path)
        self.assertLess(path.index("/opt/homebrew/bin"), path.index("/usr/bin"))

    def test_wider_scopes_are_not_enabled_by_default(self) -> None:
        for table in (self.job["environment"], self.job["launchd"]["environment"]):
            self.assertNotIn("PORTFOLIO_PURGE_REMOTE_BRANCHES", table)
            self.assertNotIn("PORTFOLIO_PURGE_IMAGES", table)


if __name__ == "__main__":
    unittest.main()
