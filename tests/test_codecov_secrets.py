"""Tests for codecov_secrets: the PAT stays in a header, the upload token on gh's stdin."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import codecov_secrets as cs  # noqa: E402

PAT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UPLOAD = "11111111-2222-3333-4444-555555555555"


def done(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    """Stands in for auto-pass and gh; records every call and the standard input."""

    def __init__(self, pat=(0, PAT + "\n", ""), secrets=None, set_error=None):
        self.pat = pat
        self.secrets = secrets if secrets is not None else {}  # "owner/repo" -> set of names
        self.set_error = set_error
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, command, stdin):
        self.calls.append((list(command), stdin))
        if command[:2] == ["auto-pass", "provision-get"]:
            return done(*self.pat)
        if command[:3] == ["gh", "secret", "list"]:
            names = self.secrets.get(command[command.index("-R") + 1])
            if names is None:
                return done(1, "", "HTTP 404")
            return done(0, json.dumps([{"name": n} for n in names]))
        if command[:3] == ["gh", "secret", "set"]:
            if self.set_error:
                return done(1, "", self.set_error)
            self.secrets.setdefault(command[command.index("-R") + 1], set()).add(command[3])
            return done(0)
        raise AssertionError(f"unexpected command {command}")

    @property
    def everything_said(self):
        return json.dumps([c for c, _ in self.calls])


class FakeHttp:
    """Codecov's API: per-repo config (upload token) and detail (active flag)."""

    def __init__(self, repos=None, status=200):
        self.repos = repos if repos is not None else {}  # name -> {"active": bool}
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, bearer):
        self.calls.append((url, bearer))
        if self.status != 200:
            return self.status, None
        tail = url.split("/repos/", 1)[1]
        name, _, rest = tail.partition("/")
        if name not in self.repos:
            return 404, None
        if rest == "config/":
            return 200, {"upload_token": UPLOAD, "graph_token": "g" * 36}
        return 200, {"name": name, "active": self.repos[name]["active"]}


def run_main(args, runner, http):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cs.main(args, run=runner, http=http)
    return code, out.getvalue(), err.getvalue()


class NameTests(unittest.TestCase):
    def test_a_usable_name_passes_and_unsafe_ones_do_not(self):
        self.assertEqual(cs.check_repo("dyno-lab"), "dyno-lab")
        for bad in ("", "../x", "a/b", "a b", "a?x=1", "-lead", "x" * 101, "a\nb"):
            with self.subTest(name=bad), self.assertRaises(cs.CodecovSecretsError):
                cs.check_repo(bad)

    def test_a_bad_name_stops_the_whole_command_before_any_call(self):
        runner, http = FakeRunner(), FakeHttp({"good": {"active": True}})
        code, _, err = run_main(["push", "--repo", "good", "--repo", "../evil"], runner, http)
        self.assertEqual(code, 2)
        self.assertEqual((runner.calls, http.calls), ([], []))
        self.assertIn("not a usable repository name", err)


class PatTests(unittest.TestCase):
    def test_each_failure_is_named_in_plain_words(self):
        cases = {
            (0, PAT, ""): "present",
            (1, "", "Could not find entry with path codecov/codecov-pat-user."): "missing",
            (1, "", "repo 'x' is not permitted to access 'y' in vault 'infra'"): "denied",
            (1, "", "daemon is locked"): "locked",
            (1, "", "boom"): "error",
        }
        for outcome, state in cases.items():
            with self.subTest(state=state):
                self.assertEqual(cs.read_pat(FakeRunner(pat=outcome)).state, state)

    def test_a_value_that_is_not_shaped_like_a_token_is_refused(self):
        for value in ("", "   ", "two words in here that is a sentence"):
            with self.subTest(value=value):
                self.assertEqual(cs.read_pat(FakeRunner(pat=(0, value, ""))).state, "error")

    def test_it_asks_the_daemon_for_exactly_the_one_entry_in_the_infra_vault(self):
        runner = FakeRunner()
        cs.read_pat(runner)
        self.assertEqual(
            runner.calls[0][0],
            ["auto-pass", "provision-get", "codecov/codecov-pat-user", "--db", "infra"],
        )


class PushTests(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp({"r": {"active": False}, "ok": {"active": True}})

    def test_the_upload_token_goes_to_standard_input_and_nowhere_else(self):
        runner = FakeRunner()
        code, out, err = run_main(["push", "--repo", "r"], runner, self.http)
        self.assertEqual(code, 0)
        sets = [(c, s) for c, s in runner.calls if c[:3] == ["gh", "secret", "set"]]
        self.assertEqual(len(sets), 1)
        command, stdin = sets[0]
        self.assertEqual(command, ["gh", "secret", "set", "CODECOV_TOKEN", "-R", "casonk/r"])
        self.assertEqual(stdin, UPLOAD)
        for secret in (PAT, UPLOAD):
            self.assertNotIn(secret, runner.everything_said)  # not in any argument list
            self.assertNotIn(secret, out + err)

    def test_the_pat_is_sent_only_as_the_bearer_of_a_codecov_request(self):
        runner = FakeRunner()
        run_main(["push", "--repo", "r"], runner, self.http)
        self.assertTrue(self.http.calls)
        for url, bearer in self.http.calls:
            self.assertEqual(bearer, PAT)
            self.assertTrue(url.startswith("https://api.codecov.io/api/v2/github/casonk/repos/r/"))
            self.assertNotIn(PAT, url)

    def test_dry_run_fetches_to_prove_it_works_but_sets_nothing(self):
        runner = FakeRunner()
        code, out, _ = run_main(["push", "--repo", "r", "--dry-run"], runner, self.http)
        self.assertEqual(code, 0)
        self.assertTrue(self.http.calls)
        self.assertFalse([c for c, _ in runner.calls if c[:3] == ["gh", "secret", "set"]])
        self.assertIn("would set CODECOV_TOKEN on casonk/r", out)
        self.assertNotIn(UPLOAD, out)

    def test_only_missing_leaves_an_existing_secret_alone_without_asking_codecov(self):
        runner = FakeRunner(secrets={"casonk/r": {"CODECOV_TOKEN"}})
        report, failures = cs.push(["r"], "casonk", runner, self.http, only_missing=True, dry_run=False)
        self.assertEqual(failures, 0)
        self.assertIn("skip r", report[0])
        self.assertEqual(self.http.calls, [])

    def test_a_repo_codecov_cannot_see_fails_alone_and_says_how_to_fix_it(self):
        runner = FakeRunner()
        report, failures = cs.push(["gone", "ok"], "casonk", runner, self.http, only_missing=False, dry_run=False)
        self.assertEqual(failures, 1)
        self.assertIn("install the Codecov GitHub app", report[0])
        self.assertEqual(report[1], "set CODECOV_TOKEN on casonk/ok")

    def test_without_the_pat_nothing_is_attempted(self):
        runner = FakeRunner(pat=(1, "", "daemon is locked"))
        report, failures = cs.push(["r"], "casonk", runner, self.http, only_missing=False, dry_run=False)
        self.assertEqual(failures, 1)
        self.assertIn("locked", report[0])
        self.assertEqual(self.http.calls, [])

    def test_codecov_errors_are_explained_without_leaking(self):
        for status, words in ((401, "rejected the PAT"), (403, "not allowed"), (0, "could not be reached"), (500, "HTTP 500")):
            with self.subTest(status=status):
                http = FakeHttp({"r": {"active": True}}, status=status)
                report, failures = cs.push(["r"], "casonk", FakeRunner(), http, only_missing=False, dry_run=False)
                self.assertEqual(failures, 1)
                self.assertIn(words, report[0])
                self.assertNotIn(PAT, report[0])

    def test_a_response_without_a_usable_token_is_a_failure(self):
        class Empty(FakeHttp):
            def __call__(self, url, bearer):
                return 200, {"graph_token": "g" * 36}

        report, failures = cs.push(["r"], "casonk", FakeRunner(), Empty(), only_missing=False, dry_run=False)
        self.assertEqual(failures, 1)
        self.assertIn("no usable upload token", report[0])

    def test_an_error_from_gh_cannot_carry_either_token_out(self):
        runner = FakeRunner(set_error=f"HTTP 422: bad value {UPLOAD} and {PAT}")
        code, out, err = run_main(["push", "--repo", "r"], runner, self.http)
        self.assertEqual(code, 1)
        for secret in (PAT, UPLOAD):
            self.assertNotIn(secret, out + err)
        self.assertIn("<token>", out)


class StatusTests(unittest.TestCase):
    def test_status_reports_every_side_without_a_value(self):
        http = FakeHttp({"have": {"active": True}, "new": {"active": False}})
        runner = FakeRunner(secrets={"casonk/have": {"CODECOV_TOKEN"}, "casonk/new": set(), "casonk/none": set()})
        code, out, _ = run_main(["status", "--repo", "have", "--repo", "new", "--repo", "none"], runner, http)
        self.assertEqual(code, 0)
        self.assertIn("PAT (infra/codecov/codecov-pat-user): present", out)
        self.assertIn("Codecov: has coverage; GitHub secret: set", out)
        self.assertIn("Codecov: visible, no coverage yet; GitHub secret: not set", out)
        self.assertIn("install the Codecov GitHub app", out)
        for secret in (PAT, UPLOAD):
            self.assertNotIn(secret, out)

    def test_status_without_the_pat_makes_no_codecov_call(self):
        http = FakeHttp({"r": {"active": True}})
        runner = FakeRunner(pat=(1, "", "repo 'x' is not permitted to access"))
        lines = cs.status(["r"], "casonk", runner, http)
        self.assertIn("denied", lines[0])
        self.assertIn("unknown (no PAT)", lines[1])
        self.assertEqual(http.calls, [])

    def test_status_says_unknown_when_github_cannot_be_asked(self):
        http = FakeHttp({"r": {"active": True}})
        self.assertIn("GitHub secret: unknown", cs.status(["r"], "casonk", FakeRunner(), http)[1])


if __name__ == "__main__":
    unittest.main()
