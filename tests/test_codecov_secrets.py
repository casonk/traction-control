"""Tests for codecov_secrets: the token reaches GitHub and nowhere else."""

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

TOKEN = "11111111-2222-3333-4444-555555555555"


def done(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    """Stands in for auto-pass and gh; records every call and the standard input."""

    def __init__(self, vault=None, secrets=None, set_error=None):
        self.vault = vault if vault is not None else {}  # entry -> (returncode, stdout, stderr)
        self.secrets = secrets if secrets is not None else {}  # "owner/repo" -> set of names
        self.set_error = set_error
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, command, stdin):
        self.calls.append((list(command), stdin))
        if command[:2] == ["auto-pass", "provision-get"]:
            return done(*self.vault.get(command[2], (1, "", "Could not find entry with path x.")))
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


def present(repo):
    return {f"dev/codecov/{repo}": (0, TOKEN + "\n", "")}


class EntryTests(unittest.TestCase):
    def test_the_entry_path_is_derived_from_a_validated_name(self):
        self.assertEqual(cs.entry_path("dyno-lab"), "dev/codecov/dyno-lab")
        for bad in ("", "../x", "a/b", "a b", "a*", "-lead", "x" * 101, "a\nb"):
            with self.subTest(name=bad), self.assertRaises(cs.CodecovSecretsError):
                cs.entry_path(bad)

    def test_a_bad_name_stops_the_whole_command_before_any_call(self):
        fake = FakeRunner(present("good"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cs.main(["push", "--repo", "good", "--repo", "../evil"], run=fake)
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [])


class VaultTests(unittest.TestCase):
    def read(self, outcome):
        return cs.read_vault("r", FakeRunner({"dev/codecov/r": outcome}))

    def test_each_failure_is_named_in_plain_words(self):
        self.assertEqual(self.read((0, TOKEN, "")).state, "present")
        self.assertEqual(self.read((1, "", "error: ... Could not find entry with path dev/codecov/r.")).state, "missing")
        self.assertEqual(self.read((1, "", "repo 'x' is not permitted to access 'y'")).state, "denied")
        self.assertEqual(self.read((1, "", "daemon is locked")).state, "locked")
        self.assertEqual(self.read((1, "", "boom")).state, "error")

    def test_a_value_that_is_not_shaped_like_a_token_is_refused(self):
        for value in ("", "   ", "two words in here that is a sentence"):
            with self.subTest(value=value):
                self.assertEqual(self.read((0, value, "")).state, "error")

    def test_it_asks_the_daemon_in_the_master_vault(self):
        fake = FakeRunner(present("r"))
        cs.read_vault("r", fake)
        self.assertEqual(fake.calls[0][0], ["auto-pass", "provision-get", "dev/codecov/r", "--db", "master"])


class PushTests(unittest.TestCase):
    def test_the_token_goes_to_standard_input_and_nowhere_else(self):
        fake = FakeRunner(present("r"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cs.main(["push", "--repo", "r"], run=fake)
        self.assertEqual(code, 0)
        sets = [(c, s) for c, s in fake.calls if c[:3] == ["gh", "secret", "set"]]
        self.assertEqual(len(sets), 1)
        command, stdin = sets[0]
        self.assertEqual(command, ["gh", "secret", "set", "CODECOV_TOKEN", "-R", "casonk/r"])
        self.assertEqual(stdin, TOKEN)
        self.assertNotIn(TOKEN, fake.everything_said)  # not in any argument list
        self.assertNotIn(TOKEN, out.getvalue())

    def test_dry_run_changes_nothing_and_says_so(self):
        fake = FakeRunner(present("r"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cs.main(["push", "--repo", "r", "--dry-run"], run=fake)
        self.assertEqual(code, 0)
        self.assertFalse([c for c, _ in fake.calls if c[:3] == ["gh", "secret", "set"]])
        self.assertIn("would set CODECOV_TOKEN on casonk/r", out.getvalue())

    def test_only_missing_leaves_an_existing_secret_alone(self):
        fake = FakeRunner(present("r"), secrets={"casonk/r": {"CODECOV_TOKEN"}})
        report, failures = cs.push(["r"], "casonk", fake, only_missing=True, dry_run=False)
        self.assertEqual(failures, 0)
        self.assertIn("skip r", report[0])
        self.assertFalse([c for c, _ in fake.calls if c[:3] == ["gh", "secret", "set"]])

    def test_a_missing_entry_is_a_failure_for_that_repo_and_the_others_still_run(self):
        fake = FakeRunner(present("ok"))
        report, failures = cs.push(["gone", "ok"], "casonk", fake, only_missing=False, dry_run=False)
        self.assertEqual(failures, 1)
        self.assertTrue(report[0].startswith("FAIL gone: missing"))
        self.assertEqual(report[1], "set CODECOV_TOKEN on casonk/ok")

    def test_an_error_from_gh_cannot_carry_the_token_out(self):
        fake = FakeRunner(present("r"), set_error=f"HTTP 422: bad value {TOKEN}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cs.main(["push", "--repo", "r"], run=fake)
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertIn("<token>", out.getvalue())

    def test_owner_is_configurable(self):
        fake = FakeRunner(present("r"))
        cs.push(["r"], "someone", fake, only_missing=False, dry_run=False)
        self.assertIn("someone/r", [part for c, _ in fake.calls for part in c])


class StatusTests(unittest.TestCase):
    def test_status_reports_both_sides_without_a_value(self):
        fake = FakeRunner(present("have"), secrets={"casonk/have": set(), "casonk/none": set()})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cs.main(["status", "--repo", "have", "--repo", "none"], run=fake)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("have", text)
        self.assertIn("vault entry: present", text)
        self.assertIn("vault entry: missing", text)
        self.assertIn("GitHub secret: not set", text)
        self.assertNotIn(TOKEN, text)

    def test_status_says_unknown_when_github_cannot_be_asked(self):
        fake = FakeRunner(present("r"))  # no secrets entry: gh answers 404
        self.assertIn("GitHub secret: unknown", cs.status(["r"], "casonk", fake)[0])


if __name__ == "__main__":
    unittest.main()
