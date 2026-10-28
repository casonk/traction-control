"""Tests for the conservative repository-description checker."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_repo_description.py"
sys.path.insert(0, str(SCRIPT.parent))

import check_repo_description as checker  # noqa: E402


class DescriptionRuleTests(unittest.TestCase):
    def check(self, text: str, name: str = "sample-repo"):
        return checker.check_description(text, name)

    def test_informative_descriptions_are_clean(self) -> None:
        for text in (
            "WireGuard VPN setup and configuration utility",
            "Shared Caddy, mTLS, and DNS provisioning for the home server portfolio",
            "A library for (so)cial (net)work (sim)ulation",
            "Tool-assisted review of utility bills.",  # 'utility' mid-sentence is fine
        ):
            self.assertEqual(self.check(text), ([], []), text)

    def test_filler_openers_are_errors(self) -> None:
        for text in (
            "Repository for coursework",
            "Utility repo for networking",
            "A utility repository to manage things",
        ):
            errors, _ = self.check(text)
            self.assertTrue(errors, text)

    def test_empty_description_is_an_error(self) -> None:
        self.assertEqual(self.check("   ")[0], ["description is empty"])

    def test_scaffold_text_needs_several_store_sentences(self) -> None:
        scaffold = "Store the portfolio-wide AGENTS.md. Store the portfolio-wide CHATHISTORY.md."
        self.assertTrue(self.check(scaffold)[0])
        self.assertEqual(self.check("Store encrypted secrets for the portfolio."), ([], []))

    def test_warnings_do_not_fail(self) -> None:
        errors, warnings = self.check("sample-repo is a messaging relay for scripts.")
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(self.check("Github Pages template forked from upstream")[1])
        self.assertTrue(self.check("x" * 40 + " " + "word " * 40)[1])


class DescriptionCliTests(unittest.TestCase):
    def run_cli(self, description: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "sample-repo"
            (repo / ".github").mkdir(parents=True)
            if description is not None:
                (repo / ".github" / "repository.toml").write_text(
                    f'[repository]\ndescription = "{description}"\n', encoding="utf-8"
                )
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--repo", str(repo)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_exit_codes_and_output(self) -> None:
        ok = self.run_cli("Local Bitcoin Stratum proxy for solo mining")
        self.assertEqual((ok.returncode, ok.stdout), (0, ""))
        bad = self.run_cli("Repository for notes")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("filler wording", bad.stdout)
        warn = self.run_cli("sample-repo is a thing that does a useful job")
        self.assertEqual(warn.returncode, 0)
        self.assertTrue(warn.stdout.startswith("warning:"))

    def test_missing_settings_file_is_not_this_checkers_finding(self) -> None:
        self.assertEqual(self.run_cli(None).returncode, 0)


if __name__ == "__main__":
    unittest.main()
