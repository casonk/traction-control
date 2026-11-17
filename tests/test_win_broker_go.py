"""Runs the Windows runner broker's Go tests when a Go toolchain is available (skipped otherwise)."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1] / "services" / "win-broker"


@unittest.skipUnless(shutil.which("go"), "Go is not installed")
class WinBrokerGoTests(unittest.TestCase):
    def run_go(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["go", *args],
            cwd=SERVICE,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
            env={**__import__("os").environ, "GOFLAGS": "-mod=readonly"},
        )

    def test_vet(self) -> None:
        proc = self.run_go("vet", "./...")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_unit_tests(self) -> None:
        proc = self.run_go("test", "-count=1", "-timeout", "120s", "./...")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class WinBrokerFilesTests(unittest.TestCase):
    def test_module_is_pinned_and_tidy_files_exist(self) -> None:
        self.assertTrue((SERVICE / "go.mod").is_file())
        self.assertTrue((SERVICE / "go.sum").is_file())
        text = (SERVICE / "go.mod").read_text(encoding="utf-8")
        self.assertIn("github.com/actions/scaleset v0.4.0", text)

    def test_container_runs_as_non_root_and_builds_static(self) -> None:
        text = (SERVICE / "Containerfile").read_text(encoding="utf-8")
        self.assertIn("CGO_ENABLED=0", text)
        self.assertIn("USER 65532", text)


if __name__ == "__main__":
    unittest.main()
