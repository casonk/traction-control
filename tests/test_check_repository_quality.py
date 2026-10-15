"""Regression tests for the staged repository quality contract."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_repository_quality.py"
BASE_FILES = (
    ".gitignore",
    ".editorconfig",
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "LESSONSLEARNED.md",
    "BACKLOG.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "REFS-PUBLIC.md",
    "docs/contributor-architecture-blueprint.md",
    "docs/diagrams/repo-architecture.puml",
    "docs/diagrams/repo-architecture.drawio",
)


class RepositoryQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for name in BASE_FILES:
            self.write(name)
        self.stage()

    def write(self, name: str, content: str = "example\n") -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def stage(self) -> None:
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)

    def check(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_base_accepts_tracked_scaffold_and_diagram_renders(self) -> None:
        self.write("docs/diagrams/repo-architecture.puml.svg")
        self.stage()
        self.assertEqual(self.check().returncode, 0)

    def test_untracked_required_file_does_not_satisfy_contract(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), "rm", "--cached", "README.md"],
            check=True,
            capture_output=True,
        )
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("required tracked file missing: README.md", result.stderr)

    def test_public_references_must_be_tracked(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), "rm", "--cached", "REFS-PUBLIC.md"],
            check=True,
            capture_output=True,
        )
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("required tracked file missing: REFS-PUBLIC.md", result.stderr)

    def test_empty_index_is_a_repository_gap(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), "rm", "--cached", "-r", "."],
            check=True,
            capture_output=True,
        )
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Git index has no tracked files", result.stderr)

    def test_rejects_runtime_files_and_orphaned_render(self) -> None:
        self.write("CHATHISTORY.md")
        self.write("cache/__pycache__/module.pyc")
        self.write("docs/diagrams/missing.puml.svg")
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("CHATHISTORY.md", result.stderr)
        self.assertIn("module.pyc", result.stderr)
        self.assertIn("render has no tracked source", result.stderr)

    def test_code_profile_and_repository_artifact_prefix(self) -> None:
        result = self.check("--profile", "code")
        self.assertEqual(result.returncode, 1)
        self.assertIn("tracked tests/", result.stderr)
        self.assertIn("tracked CI workflow", result.stderr)
        self.write(".pre-commit-config.yaml")
        self.write("tests/test_example.py")
        self.write(".github/workflows/ci.yml")
        self.write("artifacts/production.json")
        self.stage()
        self.assertEqual(self.check("--profile", "code").returncode, 0)
        result = self.check("--profile", "code", "--forbid-prefix", "artifacts/")
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "repository artifact path is tracked: artifacts/production.json",
            result.stderr,
        )

    def test_required_symlink_is_rejected(self) -> None:
        (self.repo / "README.md").unlink()
        (self.repo / "README.md").symlink_to("AGENTS.md")
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("required file must be a regular file: README.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
