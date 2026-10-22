"""Candidate discovery keeps reference fixes out of generated replay tasks."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "discover_test_replays.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("discover_test_replays", SCRIPT)
assert SPEC and SPEC.loader
replays = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replays)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


class ReplayDiscoveryTests(unittest.TestCase):
    def test_discovers_test_and_source_changes_from_pr_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "value.py").write_text("VALUE = 1\n")
            (repo / "tests" / "test_value.py").write_text(
                "def test_one():\n    assert True\n"
            )
            git(repo, "add", ".")
            git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "base",
            )
            before = git(repo, "rev-parse", "HEAD")
            (repo / "src" / "value.py").write_text("VALUE = 2\n")
            with (repo / "tests" / "test_value.py").open("a") as stream:
                stream.write("\ndef test_two():\n    assert True\n")
            git(repo, "add", ".")
            git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "fix: value (#7)",
            )
            after = git(repo, "rev-parse", "HEAD")

            cases = replays.discover(repo, "example/repo", 10)
            self.assertEqual(len(cases), 1)
            case = cases[0]
            self.assertEqual(
                (case["before"], case["after"], case["pr_hint"]), (before, after, 7)
            )
            self.assertEqual(case["test_paths"], ["tests/test_value.py"])
            self.assertEqual(case["editable_paths"], ["src/value.py"])

            output = root / "out"
            replays.write_candidates(output, cases, {"example/repo": "util-repos/repo"})
            manifest = json.loads((output / case["task_file"]).read_text())
            self.assertEqual(manifest["evolution"]["before"], before)
            self.assertEqual(manifest["editable_paths"], ["src/value.py"])
            self.assertNotIn("VALUE = 2", json.dumps(manifest))
            evidence_path = output / "origin-evidence.local.json"
            evidence = json.loads(evidence_path.read_text())
            self.assertEqual(
                evidence["cases"][case["id"]],
                {
                    "status": "unknown",
                    "model": None,
                    "evidence": [],
                    "checked_sources": [],
                },
            )
            evidence["cases"][case["id"]] = {
                "status": "verified",
                "model": "example-model",
                "evidence": ["local-session-record"],
                "checked_sources": [],
            }
            evidence_path.write_text(json.dumps(evidence))
            replays.write_candidates(output, cases, {"example/repo": "util-repos/repo"})
            preserved = json.loads(evidence_path.read_text())
            self.assertEqual(
                preserved["cases"][case["id"]], evidence["cases"][case["id"]]
            )
            self.assertTrue(
                json.loads((output / "candidate-suite.json").read_text())[
                    "status"
                ].startswith("unreviewed")
            )

    def test_explicit_checkout_rejects_unregistered_remote(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "portfolio"
            repo = root / "util-repos" / "private"
            repo.mkdir(parents=True)
            git(repo, "init", "-q")
            git(
                repo,
                "remote",
                "add",
                "origin",
                "https://github.com/example/private.git",
            )
            pair = SimpleNamespace(
                public=SimpleNamespace(
                    repositories=(
                        replays.visibility.RepositoryEntry(
                            "R_PUBLIC", "example/public"
                        ),
                    )
                )
            )
            with self.assertRaisesRegex(
                ValueError, "not in the public visibility registry"
            ):
                replays.explicit_checkouts(root, pair, ["util-repos/private"])


if __name__ == "__main__":
    unittest.main()
