"""Regression tests for scripts/repo_settings.py against a fake gh CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/repo_settings.py"
sys.path.insert(0, str(SCRIPT.parent))
import repo_settings  # noqa: E402


FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    state_path = os.environ["FAKE_GH_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[2:]
    method = "GET"
    if args[:1] == ["-X"]:
        method, args = args[1], args[2:]
    path, rest = args[0], args[1:]
    state["calls"].append([method, path])
    parts = path.split("/")
    repo = state["repos"]["/".join(parts[1:3])]
    out = None
    if len(parts) == 3 and method == "GET":
        out = repo["meta"]
    elif len(parts) == 3 and method == "PATCH":
        repo["meta"]["description"] = rest[1].split("=", 1)[1]
    elif parts[3:] == ["rulesets"] and method == "GET":
        out = [{"id": r["id"], "name": r["name"]} for r in repo["rulesets"]]
    elif parts[3:] == ["rulesets"] and method == "POST":
        body = json.load(sys.stdin)
        body["id"] = 100 + len(repo["rulesets"])
        repo["rulesets"].append(body)
    elif len(parts) == 5 and method == "GET":
        out = next(r for r in repo["rulesets"] if r["id"] == int(parts[4]))
    elif len(parts) == 5 and method == "DELETE":
        repo["rulesets"] = [r for r in repo["rulesets"] if r["id"] != int(parts[4])]
    json.dump(state, open(state_path, "w"))
    if out is not None:
        print(json.dumps(out))
    """
)


def settings_toml(description: str = "A tool.", allow_direct: bool | None = None) -> str:
    text = f'[repository]\ndescription = "{description}"\n'
    if allow_direct is not None:
        text += f"\n[main_branch]\nallow_direct = {str(allow_direct).lower()}\n"
    return text


class ParseSettingsTests(unittest.TestCase):
    def test_valid_defaults_allow_direct_false(self) -> None:
        settings = repo_settings.parse_settings(settings_toml())
        self.assertEqual(settings.description, "A tool.")
        self.assertFalse(settings.allow_direct)

    def test_rejects_invalid_files(self) -> None:
        cases = {
            "missing description": "[repository]\n",
            "empty description": '[repository]\ndescription = " "\n',
            "untrimmed description": '[repository]\ndescription = "x "\n',
            "unknown section": settings_toml() + "[extra]\nkey = 1\n",
            "unknown key": '[repository]\ndescription = "x"\nhomepage = "y"\n',
            "non-bool allow_direct": settings_toml() + '[main_branch]\nallow_direct = "yes"\n',
            "too long": settings_toml("x" * 400),
            "bad toml": "[repository\n",
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(repo_settings.SettingsError):
                repo_settings.parse_settings(text)


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        gh = self.root / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.state_path = self.root / "state.json"
        self.state = {"repos": {}, "calls": []}
        self.env = {"GH": str(gh), "FAKE_GH_STATE": str(self.state_path)}

    def add_repo(self, name: str, visibility: str, settings: str | None, description: str = "") -> Path:
        repo = self.root / "portfolio" / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", f"git@github.com:owner/{name}.git"],
            check=True,
        )
        if settings is not None:
            (repo / ".github").mkdir()
            (repo / ".github/repository.toml").write_text(settings)
        self.state["repos"][f"owner/{name}"] = {
            "meta": {
                "visibility": visibility,
                "archived": False,
                "default_branch": "main",
                "description": description,
            },
            "rulesets": [],
        }
        return repo

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.state_path.write_text(json.dumps(self.state))
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--portfolio-root", str(self.root / "portfolio"), "--worktree"],
            capture_output=True,
            text=True,
            env={**os.environ, **self.env},
        )
        self.state = json.loads(self.state_path.read_text())
        return proc

    def test_check_reports_missing_file_and_drift(self) -> None:
        self.add_repo("nofile", "private", None)
        self.add_repo("drift", "private", settings_toml("New."), description="Old.")
        proc = self.run_tool("check")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("DRIFT owner/nofile: .github/repository.toml is missing", proc.stdout)
        self.assertIn("DRIFT owner/drift: description differs", proc.stdout)

    def test_apply_sets_description_and_guards_public_repos_only(self) -> None:
        self.add_repo("pub", "public", settings_toml("Pub."))
        self.add_repo("priv", "private", settings_toml("Priv."))
        proc = self.run_tool("apply")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state["repos"]["owner/pub"]["meta"]["description"], "Pub.")
        self.assertEqual(self.state["repos"]["owner/priv"]["meta"]["description"], "Priv.")
        [ruleset] = self.state["repos"]["owner/pub"]["rulesets"]
        self.assertEqual(ruleset["name"], repo_settings.RULESET_NAME)
        self.assertEqual(ruleset["bypass_actors"], [])
        self.assertIn("pull_request", [rule["type"] for rule in ruleset["rules"]])
        self.assertEqual(self.state["repos"]["owner/priv"]["rulesets"], [])
        self.assertEqual(self.run_tool("check").returncode, 0)

    def test_allow_direct_removes_ruleset(self) -> None:
        self.add_repo("pub", "public", settings_toml("Pub.", allow_direct=True), description="Pub.")
        self.state["repos"]["owner/pub"]["rulesets"].append({"id": 7, **repo_settings.ruleset_payload()})
        self.assertEqual(self.run_tool("apply").returncode, 0)
        self.assertEqual(self.state["repos"]["owner/pub"]["rulesets"], [])

    def test_dry_run_changes_nothing(self) -> None:
        self.add_repo("pub", "public", settings_toml("Pub."))
        proc = self.run_tool("apply", "--dry-run")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("PLAN  owner/pub: description", proc.stdout)
        self.assertIn("PLAN  owner/pub: create-ruleset", proc.stdout)
        self.assertEqual(self.state["repos"]["owner/pub"]["meta"]["description"], "")
        self.assertNotIn("POST", [method for method, _ in self.state["calls"]])

    def test_archived_and_non_github_repos_are_skipped(self) -> None:
        self.add_repo("old", "public", None)
        self.state["repos"]["owner/old"]["meta"]["archived"] = True
        local = self.root / "portfolio" / "local"
        subprocess.run(["git", "init", "-q", str(local)], check=True)
        self.assertEqual(self.run_tool("check").returncode, 0)

    def test_remote_url_forms(self) -> None:
        for url in (
            "git@github.com:owner/repo.git",
            "https://github.com/owner/repo",
            "https://github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
        ):
            match = repo_settings.GITHUB_REMOTE_RE.match(url)
            self.assertIsNotNone(match, url)
            self.assertEqual((match["owner"], match["name"]), ("owner", "repo"))
        self.assertIsNone(repo_settings.GITHUB_REMOTE_RE.match("git@gitlab.com:owner/repo.git"))


if __name__ == "__main__":
    unittest.main()
