"""Tests for actions_switch: what it changes, what it leaves alone, what it reports."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import actions_switch as sw  # noqa: E402

OCT = datetime(2026, 10, 20, tzinfo=timezone.utc)
NOV = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)


class FakeGitHub:
    """In-memory stand-in for the REST calls; records every write."""

    def __init__(self, repos: dict[str, dict], usage: dict | None = None) -> None:
        self.repos = repos  # slug -> {archived, enabled, workflows: {file: state}}
        self.usage = usage
        self.writes: list[tuple[str, str, dict | None]] = []

    def api(self, method, path, fields=None):
        if method != "GET":
            self.writes.append((method, path, fields))
        parts = path.split("?")[0].split("/")
        if parts[0] == "users":
            if self.usage is None:
                raise sw.GitHubError("needs the user scope")
            return self.usage
        slug = "/".join(parts[1:3])
        repo = self.repos[slug]
        rest = "/".join(parts[3:])
        if method == "GET" and not rest:
            return {"archived": repo["archived"]}
        if rest == "actions/permissions":
            if method == "PUT":
                repo["enabled"] = fields["enabled"] == "true"
                return None
            return {"enabled": repo["enabled"]}
        if rest == "actions/workflows":
            return {
                "workflows": [
                    {"path": f".github/workflows/{n}", "state": s} for n, s in repo["workflows"].items()
                ]
            }
        if method == "PUT" and rest.endswith("/enable"):
            repo["workflows"][rest.split("/")[2]] = "active"
            return None
        raise AssertionError(f"unexpected call {method} {path}")


def repo(enabled=True, archived=False, **workflows):
    return {"enabled": enabled, "archived": archived, "workflows": workflows}


def ledger():
    return {"disabled": {}, "notified": {}}


class SelectCase(unittest.TestCase):
    universe = ["me/alpha", "me/Beta", "me/.github"]

    def test_everything_when_empty(self):
        self.assertEqual(sw.select(self.universe, []), self.universe)

    def test_slug_or_bare_name_any_case(self):
        self.assertEqual(sw.select(self.universe, ["BETA", "me/alpha", "beta"]), ["me/Beta", "me/alpha"])

    def test_unknown_name_is_an_error(self):
        with self.assertRaisesRegex(sw.SwitchError, "nope"):
            sw.select(self.universe, ["alpha", "nope"])


class EnableCase(unittest.TestCase):
    def test_turns_on_and_reenables_inactivity_workflows(self):
        gh = FakeGitHub({"me/a": repo(False, **{"scan.yml": sw.INACTIVITY})})
        led = ledger()
        led["disabled"]["me/a"] = {"at": OCT.isoformat(), "reason": "x"}
        report, failures = sw.enable(gh, ["me/a"], led)
        self.assertEqual(failures, 0)
        self.assertTrue(gh.repos["me/a"]["enabled"])
        self.assertEqual(gh.repos["me/a"]["workflows"]["scan.yml"], "active")
        self.assertNotIn("me/a", led["disabled"])
        self.assertIn("enabled me/a", report)

    def test_already_on_repo_still_gets_stuck_workflow_fixed(self):
        gh = FakeGitHub({"me/a": repo(True, **{"scan.yml": sw.INACTIVITY})})
        sw.enable(gh, ["me/a"], ledger())
        self.assertEqual(gh.repos["me/a"]["workflows"]["scan.yml"], "active")
        self.assertFalse(any(p.endswith("actions/permissions") for _, p, _ in gh.writes))

    def test_manual_disable_is_left_unless_asked(self):
        gh = FakeGitHub({"me/a": repo(True, **{"keep.yml": sw.MANUAL})})
        report, _ = sw.enable(gh, ["me/a"], ledger())
        self.assertEqual(gh.repos["me/a"]["workflows"]["keep.yml"], sw.MANUAL)
        self.assertIn(f"left me/a keep.yml ({sw.MANUAL})", report)
        sw.enable(gh, ["me/a"], ledger(), include_manual=True)
        self.assertEqual(gh.repos["me/a"]["workflows"]["keep.yml"], "active")

    def test_archived_is_skipped_without_a_write(self):
        gh = FakeGitHub({"me/old": repo(False, archived=True)})
        report, failures = sw.enable(gh, ["me/old"], ledger())
        self.assertEqual((gh.writes, failures), ([], 0))
        self.assertIn("skip me/old: archived", report)

    def test_dry_run_changes_nothing(self):
        gh = FakeGitHub({"me/a": repo(False, **{"scan.yml": sw.INACTIVITY})})
        led = ledger()
        led["disabled"]["me/a"] = {"at": OCT.isoformat(), "reason": "x"}
        report, _ = sw.enable(gh, ["me/a"], led, dry_run=True)
        self.assertEqual(gh.writes, [])
        self.assertIn("me/a", led["disabled"])
        self.assertIn("would enable me/a", report)

    def test_one_failure_does_not_stop_the_rest(self):
        gh = FakeGitHub({"me/a": repo(False), "me/b": repo(False)})
        original = gh.api

        def flaky(method, path, fields=None):
            if method == "PUT" and "me/a/" in path:
                raise sw.GitHubError("403 forbidden")
            return original(method, path, fields)

        gh.api = flaky
        report, failures = sw.enable(gh, ["me/a", "me/b"], ledger())
        self.assertEqual(failures, 1)
        self.assertTrue(gh.repos["me/b"]["enabled"])
        self.assertTrue(any(line.startswith("FAIL me/a") for line in report))


class DisableCase(unittest.TestCase):
    def test_records_why_and_when(self):
        gh = FakeGitHub({"me/a": repo(True)})
        led = ledger()
        sw.disable(gh, ["me/a"], led, reason="minutes exhausted", now=OCT)
        self.assertFalse(gh.repos["me/a"]["enabled"])
        self.assertEqual(led["disabled"]["me/a"], {"at": OCT.isoformat(), "reason": "minutes exhausted"})

    def test_already_off_keeps_the_original_record(self):
        gh = FakeGitHub({"me/a": repo(False)})
        led = ledger()
        led["disabled"]["me/a"] = {"at": "2026-09-01T00:00:00+00:00", "reason": "first"}
        sw.disable(gh, ["me/a"], led, reason="second", now=OCT)
        self.assertEqual(led["disabled"]["me/a"]["reason"], "first")

    def test_dry_run_and_archived_write_nothing(self):
        gh = FakeGitHub({"me/a": repo(True), "me/old": repo(False, archived=True)})
        led = ledger()
        sw.disable(gh, ["me/a", "me/old"], led, reason="r", now=OCT, dry_run=True)
        self.assertEqual((gh.writes, led["disabled"]), ([], {}))


class UsageCase(unittest.TestCase):
    ITEMS = {
        "usageItems": [
            {"product": "Actions", "unitType": "Minutes", "quantity": 1500, "discountQuantity": 1500, "netQuantity": 0},
            {"product": "Actions", "unitType": "Minutes", "quantity": 600, "discountQuantity": 500, "netQuantity": 100},
            {"product": "Actions", "unitType": "GigabyteHours", "quantity": 99},
            {"product": "Copilot", "unitType": "Requests", "quantity": 7},
        ]
    }

    def test_sums_only_actions_minutes(self):
        usage = sw.fetch_usage(FakeGitHub({}, self.ITEMS), "me", "2026-11")
        self.assertEqual((usage.minutes, usage.net_minutes), (2100, 100))

    def test_missing_scope_says_how_to_fix_it(self):
        with self.assertRaisesRegex(sw.GitHubError, "gh auth refresh"):
            sw.fetch_usage(FakeGitHub({}, None), "me", "2026-11")


class AssessCase(unittest.TestCase):
    def assess(self, states, led=None, usage=None, now=NOV, **kw):
        return sw.assess(states, led or ledger(), now, usage, **kw)

    def test_off_with_no_record_is_flagged(self):
        findings, _ = self.assess([sw.RepoState("me/a")])
        self.assertEqual([f.key for f in findings], ["unrecorded:me/a"])

    def test_pause_from_an_earlier_month_is_flagged_but_this_month_is_not(self):
        led = ledger()
        led["disabled"]["me/a"] = {"at": OCT.isoformat(), "reason": "minutes"}
        findings, paused = self.assess([sw.RepoState("me/a")], led)
        self.assertEqual([f.key for f in findings], ["rolled:me/a"])
        findings, paused = self.assess([sw.RepoState("me/a")], led, now=OCT)
        self.assertEqual((findings, paused), ([], ["me/a"]))

    def test_year_rollover_counts_as_a_new_month(self):
        led = ledger()
        led["disabled"]["me/a"] = {"at": "2026-12-20T00:00:00+00:00", "reason": "m"}
        january = datetime(2027, 1, 1, tzinfo=timezone.utc)
        findings, _ = self.assess([sw.RepoState("me/a")], led, now=january)
        self.assertEqual(len(findings), 1)

    def test_stuck_inactivity_workflow_but_not_a_manual_one(self):
        on = sw.RepoState("me/a", enabled=True, workflows=[(".github/workflows/s.yml", sw.INACTIVITY), (".github/workflows/m.yml", sw.MANUAL)])
        findings, _ = self.assess([on])
        self.assertEqual([f.key for f in findings], ["stuck:me/a:.github/workflows/s.yml"])

    def test_archived_and_healthy_are_quiet(self):
        findings, paused = self.assess([sw.RepoState("me/old", archived=True), sw.RepoState("me/a", enabled=True)])
        self.assertEqual((findings, paused), ([], []))

    def test_usage_threshold(self):
        states = [sw.RepoState("me/a", enabled=True)]
        low, _ = self.assess(states, usage=sw.Usage("2026-11", 1000, 0), limit_minutes=2000)
        high, _ = self.assess(states, usage=sw.Usage("2026-11", 1700, 0), limit_minutes=2000)
        self.assertEqual(low, [])
        self.assertEqual([f.key for f in high], ["usage:2026-11:80"])


class CheckCase(unittest.TestCase):
    ENV = {"ACTIONS_SWITCH_NOTIFY_SIGNAL_TARGET": "+10000000000", "ACTIONS_SWITCH_NOTIFY_GMAIL_TARGET": "o@example.com"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "state"
        self.sent = []

    def sender(self, root, route, subject, body):
        self.sent.append((route.service, subject, body))
        return None

    def run_check(self, findings, led, now=1_000_000.0, **kw):
        return sw.check(
            findings, led, self.dir, ("signal", "telegram", "gmail"), 5,
            now=now, renotify_hours=24, notify=True, sender=self.sender, environ=self.ENV, **kw,
        )

    def test_one_message_for_everything_and_a_skipped_channel_is_reported(self):
        findings = [sw.Finding("a", "me/a: off"), sw.Finding("b", "me/b: off")]
        code, report = self.run_check(findings, ledger())
        self.assertEqual(code, 1)
        self.assertEqual(sorted(s for s, _, _ in self.sent), ["gmail", "signal"])
        self.assertIn("me/a: off", self.sent[0][2])
        self.assertIn("me/b: off", self.sent[0][2])
        self.assertTrue(any("telegram: no ACTIONS_SWITCH_NOTIFY_TELEGRAM_TARGET" in r for r in report))

    def test_repeats_only_after_the_interval(self):
        findings, led = [sw.Finding("a", "me/a: off")], ledger()
        self.run_check(findings, led)
        self.sent.clear()
        self.run_check(findings, led, now=1_000_000.0 + 3600)
        self.assertEqual(self.sent, [])
        self.run_check(findings, led, now=1_000_000.0 + 25 * 3600)
        self.assertTrue(self.sent)

    def test_resolved_finding_announces_again_if_it_recurs(self):
        led = ledger()
        self.run_check([sw.Finding("a", "x")], led)
        self.run_check([], led, now=1_000_100.0)
        self.sent.clear()
        self.run_check([sw.Finding("a", "x")], led, now=1_000_200.0)
        self.assertTrue(self.sent)

    def test_failed_delivery_is_retried(self):
        led = ledger()
        code, report = sw.check(
            [sw.Finding("a", "x")], led, self.dir, ("signal",), 1, now=1_000_000.0, renotify_hours=24,
            notify=True, sender=lambda *a: "boom", environ=self.ENV,
        )
        self.assertEqual(led["notified"], {})
        self.assertIn("no channel delivered; will retry", report)

    def test_without_notify_nothing_is_sent_and_exit_reflects_findings(self):
        code, _ = sw.check([sw.Finding("a", "x")], ledger(), self.dir, (), 1, now=1.0, renotify_hours=24, notify=False, sender=self.sender)
        self.assertEqual((code, self.sent), (1, []))
        code, report = sw.check([], ledger(), self.dir, (), 1, now=1.0, renotify_hours=24, notify=False)
        self.assertEqual((code, report), (0, ["nothing needs attention"]))

    def test_long_lists_are_capped(self):
        subject, body = sw.compose([sw.Finding(str(i), f"r{i}") for i in range(14)], 50)
        self.assertIn("(14)", subject)
        self.assertIn("... and 4 more", body)
        self.assertNotIn("r13", body)


class LedgerCase(unittest.TestCase):
    def test_round_trip_is_owner_only_and_tolerates_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "s"
            self.assertEqual(sw.load_ledger(state), ledger())
            led = ledger()
            led["disabled"]["me/a"] = {"at": OCT.isoformat(), "reason": "r"}
            sw.save_ledger(state, led)
            self.assertEqual(sw.load_ledger(state), led)
            self.assertEqual((state / sw.STATE_FILE).stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            (state / sw.STATE_FILE).write_text("not json")
            self.assertEqual(sw.load_ledger(state), ledger())


class CliCase(unittest.TestCase):
    def run_cli(self, gh, *argv):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(out), redirect_stderr(err):
            self.state = Path(tmp) / "state"
            code = sw.main(["--owner", "me", "--state-dir", str(self.state), *argv], github=gh)
            self.ledger = sw.load_ledger(self.state)
        return code, out.getvalue(), err.getvalue()

    def make(self, **repos):
        gh = FakeGitHub(repos)
        original = gh.api

        def api(method, path, fields=None):
            if path.startswith("users/me/repos"):
                return [{"full_name": slug} for slug in gh.repos]
            return original(method, path, fields)

        gh.api = api
        return gh

    def test_disabling_everything_needs_yes(self):
        gh = self.make(**{"me/a": repo(True)})
        code, _, err = self.run_cli(gh, "disable")
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        self.assertTrue(gh.repos["me/a"]["enabled"])

    def test_disable_yes_then_enable_round_trip(self):
        gh = self.make(**{"me/a": repo(True), "me/b": repo(True)})
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            args = ["--owner", "me", "--state-dir", tmp]
            self.assertEqual(sw.main([*args, "disable", "--yes"], github=gh), 0)
            self.assertEqual(set(sw.load_ledger(Path(tmp))["disabled"]), {"me/a", "me/b"})
            self.assertEqual(sw.main([*args, "enable", "--repo", "a"], github=gh), 0)
            self.assertEqual(set(sw.load_ledger(Path(tmp))["disabled"]), {"me/b"})
        self.assertEqual((gh.repos["me/a"]["enabled"], gh.repos["me/b"]["enabled"]), (True, False))

    def test_status_lists_state(self):
        gh = self.make(**{"me/a": repo(False), "me/b": repo(True, **{"s.yml": sw.INACTIVITY})})
        code, out, _ = self.run_cli(gh, "status")
        self.assertEqual(code, 0)
        self.assertIn("OFF  (no recorded reason)", out)
        self.assertIn("s.yml disabled_inactivity", out)
        self.assertIn("2 repositories: 1 off, 1 with inactive workflows", out)

    def test_unknown_repo_exits_2(self):
        code, _, err = self.run_cli(self.make(**{"me/a": repo()}), "status", "--repo", "zzz")
        self.assertEqual(code, 2)
        self.assertIn("zzz", err)

    def test_check_exit_codes(self):
        code, out, _ = self.run_cli(self.make(**{"me/a": repo(True)}), "check", "--no-usage")
        self.assertEqual((code, out.strip()), (0, "nothing needs attention"))
        code, out, _ = self.run_cli(self.make(**{"me/a": repo(False)}), "check", "--no-usage")
        self.assertEqual(code, 1)
        self.assertIn("no recorded reason", out)

    def test_missing_registry_is_a_clean_error(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(out), redirect_stderr(err):
            missing = str(Path(tmp) / "none.json")
            code = sw.main(["--private", missing, "--public", missing, "--state-dir", tmp, "status"], github=FakeGitHub({}))
        self.assertEqual(code, 2)
        self.assertIn("visibility registry", err.getvalue())


if __name__ == "__main__":
    unittest.main()
