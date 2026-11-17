"""Tests for scripts/install_azure_runner_scale_sets.sh (offline: --dry-run and --print-script only)."""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_azure_runner_scale_sets.sh"


def run(*args: str, slugs: list[str]) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as raw:
        slug_file = Path(raw) / "slugs.txt"
        slug_file.write_text("\n".join(slugs) + "\n", encoding="utf-8")
        return subprocess.run(
            ["bash", str(SCRIPT), "--slugs-file", str(slug_file), *args],
            capture_output=True,
            text=True,
            check=False,
        )


class DryRunTests(unittest.TestCase):
    def test_names_and_plan(self) -> None:
        proc = run("--dry-run", slugs=["o/Alpha", "o/beta_two", "o/gamma.x"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("alpha-azure", proc.stdout)
        self.assertIn("beta-two-azure", proc.stdout)
        self.assertIn("gamma-x-azure", proc.stdout)
        self.assertIn("nothing was sent to Azure", proc.stdout)

    def test_repo_filter_and_unknown_repo(self) -> None:
        proc = run("--dry-run", "--repo", "beta_two", slugs=["o/Alpha", "o/beta_two"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("beta-two-azure", proc.stdout)
        self.assertNotIn("alpha-azure", proc.stdout)
        bad = run("--dry-run", "--repo", "a-public-repo", slugs=["o/Alpha"])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("not a private repository", bad.stderr)

    def test_unknown_argument_is_refused(self) -> None:
        self.assertNotEqual(run("--nope", slugs=["o/a"]).returncode, 0)


class RemoteScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        proc = run("--print-script", "--repo", "alpha", slugs=["o/alpha", "o/beta"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.script = proc.stdout

    def test_it_parses_as_shell(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as handle:
            handle.write(self.script)
            handle.flush()
            self.assertEqual(subprocess.run(["bash", "-n", handle.name]).returncode, 0)

    def test_values_are_rendered_for_the_vm(self) -> None:
        match = re.search(r"^VALUES_0_B64='([^']+)'", self.script, re.MULTILINE)
        self.assertIsNotNone(match)
        values = base64.b64decode(match.group(1)).decode()
        self.assertIn("githubConfigUrl: https://github.com/o/alpha", values)
        self.assertIn("runnerScaleSetName: alpha-azure", values)
        self.assertIn("githubConfigSecret: runner-auth", values)
        self.assertIn("image: arc-runner:", values)
        self.assertNotIn("localhost:5000", values)
        self.assertIn("memory: 1536Mi", values)
        self.assertIn("maxRunners: 1", values)

    def test_only_the_selected_repository_is_included(self) -> None:
        self.assertIn("COUNT=1", self.script)
        self.assertIn("NAME_0='alpha-azure'", self.script)
        self.assertNotIn("beta-azure", self.script)

    def test_remote_guards_are_present(self) -> None:
        for needle in (
            "runner-auth",  # refuses to run without the credential Secret
            "arc-gha-rs-controller",  # refuses to run without the controller
            "pod-security.kubernetes.io/enforce=restricted",
            'pods: "2"',
            "__SCALE_SETS_EXIT_STATUS",
            "__SCALE_SET_OK",
        ):
            self.assertIn(needle, self.script)

    def test_no_credentials_are_embedded(self) -> None:
        self.assertNotRegex(self.script, r"ghp_|github_pat_|BEGIN [A-Z ]*PRIVATE KEY")


if __name__ == "__main__":
    unittest.main()
