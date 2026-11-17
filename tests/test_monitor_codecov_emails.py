"""Tests for scripts/monitor_codecov_emails.py (offline: no Gmail, GitHub or systemd)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("monitor_codecov_emails", SCRIPTS / "monitor_codecov_emails.py")
mon = importlib.util.module_from_spec(_spec)
sys.modules["monitor_codecov_emails"] = mon
_spec.loader.exec_module(mon)

URL = "https://app.codecov.io/gh/example/widgets/pull/42"
FAILING = f"""codecov[bot] left a comment (example/widgets#42)

## [Codecov]({URL}?dropdown=coverage&src=pr&el=h1) Report
:x: Patch coverage is `71.83908%` with `49 lines` in your changes missing coverage. Please review.
| [Files with missing lines]({URL}?el=tree) | Patch % | Lines |
|---|---|---|
| [web/upscale.py]({URL}?el=tree#diff-abc) | 70.55% | [48 Missing :warning: ]({URL}?el=tree) |
| [web/library\\_pages.py]({URL}?el=tree#diff-def) | 90.90% | [1 Missing :warning: ]({URL}?el=tree) |

--
Reply to this email directly or view it on GitHub:
https://github.com/example/widgets/pull/42#issuecomment-6931729597
"""
COVERED = """codecov[bot] left a comment (example/widgets#42)

## [Codecov](https://app.codecov.io/gh/example/widgets/pull/42) Report
:white_check_mark: All modified and coverable lines are covered by tests.

https://github.com/example/widgets/pull/42#issuecomment-6931729999
"""
OLD_STYLE = """codecov[bot] left a comment (example/widgets#7)

# Codecov Report
Attention: `3 lines` in your changes are missing coverage. Please review.

https://github.com/example/widgets/pull/7#issuecomment-111
"""

INDEX = {"example/widgets": "util-repos/widgets"}


def message(body: str, message_id: str = "<m1>") -> dict:
    return {"message_id": message_id, "subject": "Re: [example/widgets] feat (PR #42)", "text": body, "date": "d"}


class Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class ParseTests(unittest.TestCase):
    def test_failing_comment(self) -> None:
        comment = mon.parse_codecov_comment(message(FAILING), INDEX)
        self.assertIsNotNone(comment)
        self.assertEqual((comment.repo_slug, comment.pr), ("example/widgets", 42))
        self.assertEqual(comment.repo_rel, "util-repos/widgets")
        self.assertAlmostEqual(comment.patch_pct, 71.83908)
        self.assertEqual(comment.missing_lines, 49)
        self.assertTrue(comment.failed)
        self.assertEqual(comment.comment_id, "6931729597")
        self.assertEqual([f.path for f in comment.files], ["web/upscale.py", "web/library_pages.py"])
        self.assertEqual([f.missing for f in comment.files], [48, 1])

    def test_covered_comment_needs_no_action(self) -> None:
        comment = mon.parse_codecov_comment(message(COVERED), INDEX)
        self.assertIsNotNone(comment)
        self.assertEqual(comment.missing_lines, 0)
        self.assertFalse(mon.needs_action(comment, None))

    def test_old_attention_format(self) -> None:
        comment = mon.parse_codecov_comment(message(OLD_STYLE), INDEX)
        self.assertEqual(comment.missing_lines, 3)
        self.assertTrue(mon.needs_action(comment, None))

    def test_other_github_mail_is_ignored(self) -> None:
        self.assertIsNone(mon.parse_codecov_comment(message("someone left a comment (a/b#1)"), INDEX))
        self.assertIsNone(mon.parse_codecov_comment(message("codecov[bot] left a comment (a/b#1) unrelated"), INDEX))

    def test_min_patch_floor_applies_when_codecov_did_not_flag(self) -> None:
        body = FAILING.replace(":x:", ":warning:")
        comment = mon.parse_codecov_comment(message(body), INDEX)
        self.assertFalse(comment.failed)
        self.assertFalse(mon.needs_action(comment, None))
        self.assertTrue(mon.needs_action(comment, 80.0))
        self.assertFalse(mon.needs_action(comment, 50.0))


class BacklogTextTests(unittest.TestCase):
    def test_inserts_under_pending_once(self) -> None:
        text = "# BACKLOG.md\n\n## Pending\n\n- [ ] existing\n\n## Done\n"
        out = mon.insert_pending_item(text, "- [ ] new (codecov-pr:42)", "codecov-pr:42")
        self.assertEqual(out.splitlines()[2:6], ["## Pending", "", "- [ ] new (codecov-pr:42)", "- [ ] existing"])
        self.assertIsNone(mon.insert_pending_item(out, "- [ ] new (codecov-pr:42)", "codecov-pr:42"))

    def test_creates_the_pending_section_when_missing(self) -> None:
        out = mon.insert_pending_item("# BACKLOG.md\n", "- [ ] new", "codecov-pr:9")
        self.assertIn("## Pending\n\n- [ ] new\n", out)

    def test_heading_as_last_line(self) -> None:
        out = mon.insert_pending_item("## Pending", "- [ ] new", "codecov-pr:9")
        self.assertEqual(out, "## Pending\n\n- [ ] new\n")

    def test_line_names_pr_files_and_marker(self) -> None:
        comment = mon.parse_codecov_comment(message(FAILING), INDEX)
        line = mon.backlog_line(comment, "2026-11-17")
        self.assertIn("[codecov:2026-11-17]", line)
        self.assertIn("PR #42", line)
        self.assertIn("web/upscale.py (48 missing, 70.55%)", line)
        self.assertIn("codecov-pr:42", line)


def make_config(tmp: Path, **overrides) -> "mon.MonitorConfig":
    base = dict(
        portfolio_root=tmp,
        shock_relay_root=tmp,
        gmail_config=tmp / "g.yaml",
        state_file=tmp / "state.json",
        candidate_file=tmp / "cand.tsv",
        mailbox="INBOX",
        from_filter="notifications@github.com",
        since_days=3,
        limit=10,
        min_patch_pct=None,
        max_attempts=2,
        messages_file=None,
        add_backlog=True,
        trigger_repair=True,
        trigger_delay_minutes=15,
        trigger_service="codecov-repair-agentic.service",
        trigger_unit="codecov-repair-agentic-email-trigger",
        dry_run=False,
        output_json=False,
    )
    base.update(overrides)
    return mon.MonitorConfig(**base)


class FakeRunner:
    """Records commands; answers gh/git/systemd-run like a healthy host."""

    def __init__(self, pr_state: str = "OPEN", cross: bool = False, backlog: str = "## Pending\n\n## Done\n") -> None:
        self.calls: list[list[str]] = []
        self.pr_state, self.cross, self.backlog = pr_state, cross, backlog

    def __call__(self, args, cwd=None):
        self.calls.append(list(args))
        if args[:3] == ["git", "worktree", "add"]:
            Path(args[-2]).mkdir(parents=True, exist_ok=True)  # git creates the checkout directory
        if args[:3] == ["gh", "pr", "view"]:
            return Result(stdout=json.dumps({"state": self.pr_state, "headRefName": "feat/x", "isCrossRepository": self.cross}))
        if args[:2] == ["git", "show"]:
            return Result(stdout=self.backlog)
        if args[:3] == ["gh", "pr", "create"]:
            return Result(stdout="https://github.com/example/widgets/pull/50\n")
        return Result()

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


class ProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "util-repos" / "widgets").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def comments(self, body: str = FAILING):
        return [mon.parse_codecov_comment(message(body), INDEX)]

    def test_failing_open_pr_gets_backlog_pr_and_repair_candidate(self) -> None:
        runner = FakeRunner()
        config = make_config(self.tmp)
        state, summary = mon.process(config, self.comments(), {}, today="2026-11-17", runner=runner)
        self.assertEqual(summary["candidates"], 1)
        self.assertTrue(summary["actions"][0]["backlog"].startswith("opened"))
        self.assertEqual(summary["actions"][0]["repair"], "candidate")
        self.assertEqual(summary["trigger"], "scheduled in 15 minutes")
        self.assertEqual(len(runner.ran("systemd-run")), 1)
        rows = config.candidate_file.read_text().splitlines()
        self.assertEqual(rows[0].split("\t"), list(mon.CANDIDATE_HEADER))
        self.assertEqual(rows[1].split("\t")[1:5], ["example/widgets", "util-repos/widgets", "42", "feat/x"])
        self.assertEqual(state["prs"]["example/widgets#42"]["repair_attempts"], 1)

    def test_same_comment_is_not_processed_twice(self) -> None:
        runner = FakeRunner()
        config = make_config(self.tmp)
        state, _ = mon.process(config, self.comments(), {}, today="d", runner=runner)
        runner.calls.clear()
        _, summary = mon.process(config, self.comments(), state, today="d", runner=runner)
        self.assertEqual(summary["actions"], [])
        self.assertEqual(runner.ran("systemd-run"), [])

    def test_merged_pr_gets_backlog_but_no_repair(self) -> None:
        runner = FakeRunner(pr_state="MERGED")
        _, summary = mon.process(make_config(self.tmp), self.comments(), {}, today="d", runner=runner)
        self.assertTrue(summary["actions"][0]["backlog"].startswith("opened"))
        self.assertIn("not open", summary["actions"][0]["repair"])
        self.assertEqual(summary["candidates"], 0)
        self.assertEqual(runner.ran("systemd-run"), [])

    def test_fork_pr_is_not_repaired(self) -> None:
        _, summary = mon.process(make_config(self.tmp), self.comments(), {}, today="d", runner=FakeRunner(cross=True))
        self.assertIn("fork", summary["actions"][0]["repair"])

    def test_repair_attempts_are_capped(self) -> None:
        runner = FakeRunner()
        config = make_config(self.tmp, max_attempts=1)
        state = {"prs": {"example/widgets#42": {"repair_attempts": 1, "comment_id": "older", "backlog_pr": "x"}}}
        _, summary = mon.process(config, self.comments(), state, today="d", runner=runner)
        self.assertIn("already tried 1", summary["actions"][0]["repair"])
        self.assertEqual(summary["candidates"], 0)

    def test_existing_backlog_item_opens_no_second_pr(self) -> None:
        backlog = "## Pending\n\n- [ ] old (codecov-pr:42)\n"
        runner = FakeRunner(backlog=backlog)
        _, summary = mon.process(make_config(self.tmp), self.comments(), {}, today="d", runner=runner)
        self.assertTrue(summary["actions"][0]["backlog"].startswith("exists"))
        self.assertEqual(runner.ran("gh", "pr", "create"), [])

    def test_backlog_pr_commits_only_backlog_and_removes_the_worktree(self) -> None:
        runner = FakeRunner()
        mon.process(make_config(self.tmp), self.comments(), {}, today="2026-11-17", runner=runner)
        self.assertEqual(runner.ran("git", "add"), [["git", "add", "BACKLOG.md"]])
        self.assertEqual(len(runner.ran("git", "worktree", "remove")), 1)
        self.assertEqual(runner.ran("git", "push")[0][-1], "chore/codecov-backlog-pr42")

    def test_dry_run_changes_nothing(self) -> None:
        runner = FakeRunner()
        config = make_config(self.tmp, dry_run=True)
        _, summary = mon.process(config, self.comments(), {}, today="d", runner=runner)
        self.assertEqual(summary["actions"][0]["backlog"], "dry run")
        self.assertFalse(config.candidate_file.exists())
        self.assertEqual(runner.ran("git"), [])
        self.assertEqual(runner.ran("systemd-run"), [])

    def test_covered_comment_marks_the_pr_resolved(self) -> None:
        state = {"prs": {"example/widgets#42": {"comment_id": "1"}}}
        new_state, summary = mon.process(make_config(self.tmp), self.comments(COVERED), state, today="d", runner=FakeRunner())
        self.assertEqual(summary["actions"], [])
        self.assertIn("resolved_at", new_state["prs"]["example/widgets#42"])

    def test_repository_not_checked_out_is_reported(self) -> None:
        comment = mon.parse_codecov_comment(message(FAILING), {})
        _, summary = mon.process(make_config(self.tmp), [comment], {}, today="d", runner=FakeRunner())
        self.assertIn("not checked out", summary["actions"][0]["backlog"])
        self.assertEqual(summary["candidates"], 0)

    def test_disabled_actions(self) -> None:
        runner = FakeRunner()
        config = make_config(self.tmp, add_backlog=False, trigger_repair=False)
        _, summary = mon.process(config, self.comments(), {}, today="d", runner=runner)
        self.assertNotIn("backlog", summary["actions"][0])
        self.assertEqual(runner.calls, [])


class CliTests(unittest.TestCase):
    def test_offline_dry_run_from_messages_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "m.json").write_text(json.dumps({"messages": [message(FAILING)]}))
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "monitor_codecov_emails.py"),
                    "--messages-file",
                    str(tmp / "m.json"),
                    "--portfolio-root",
                    str(tmp),
                    "--state-file",
                    str(tmp / "s.json"),
                    "--candidate-file",
                    str(tmp / "c.tsv"),
                    "--dry-run",
                    "--no-trigger-repair",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)["summary"]
            self.assertEqual(summary["seen"], 1)
            self.assertFalse((tmp / "s.json").exists())


if __name__ == "__main__":
    unittest.main()
