#!/usr/bin/env python3
"""Keep GitHub repository settings in sync with each checkout's settings file.

GitHub has no file that sets a repository's description or branch rules; both
live only in the API. This tool makes them reviewable code: every portfolio
repository carries ``.github/repository.toml``, and ``apply`` pushes that file's
contents to GitHub.

Settings are read from the remote default branch (``origin/main``) after a
fetch, not from the working tree, so a change takes effect only once its pull
request is merged. ``--worktree`` reads the working tree instead.

File format::

    [repository]
    description = "One line shown on GitHub."

    [main_branch]
    # Direct commits and pushes to main are refused unless this is true.
    allow_direct = false

Main-branch guard:

- Public repositories get the ``main-branch-guard`` ruleset: changes reach the
  default branch only through a pull request (no approvals required, no bypass
  actors), and force-pushes and deletion are blocked.
- Private repositories on a free plan cannot enforce rulesets, so they rely on
  the local hooks installed by ``scripts/install_main_branch_guard.sh``.
- ``allow_direct = true`` removes the ruleset and lets the local hooks pass.

Commands:

- ``check``: report missing or invalid files and drift from GitHub; exit 1 on
  any finding.
- ``apply``: update GitHub to match. ``--dry-run`` prints the changes only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SETTINGS_PATH = ".github/repository.toml"
RULESET_NAME = "main-branch-guard"
MAX_DESCRIPTION = 350
SECTION_KEYS = {
    "repository": {"description"},
    "main_branch": {"allow_direct"},
}
GITHUB_REMOTE_RE = re.compile(
    r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?\Z"
)


class SettingsError(Exception):
    """Raised when a settings file is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    description: str
    allow_direct: bool


@dataclass(frozen=True)
class Remote:
    owner: str
    name: str
    visibility: str
    archived: bool
    default_branch: str
    description: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def ruleset_payload() -> dict[str, Any]:
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
        ],
    }


def parse_settings(text: str) -> Settings:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"invalid TOML: {exc}") from exc
    unknown = set(data) - set(SECTION_KEYS)
    if unknown:
        raise SettingsError(f"unknown sections: {', '.join(sorted(unknown))}")
    for section, allowed in SECTION_KEYS.items():
        value = data.get(section, {})
        if not isinstance(value, dict):
            raise SettingsError(f"[{section}] must be a table")
        extra = set(value) - allowed
        if extra:
            raise SettingsError(f"unknown keys in [{section}]: {', '.join(sorted(extra))}")
    description = data.get("repository", {}).get("description")
    if not isinstance(description, str) or not description.strip():
        raise SettingsError("[repository] description must be a non-empty string")
    if description != description.strip() or "\n" in description:
        raise SettingsError("[repository] description must be one trimmed line")
    if len(description) > MAX_DESCRIPTION:
        raise SettingsError(f"[repository] description exceeds {MAX_DESCRIPTION} characters")
    allow_direct = data.get("main_branch", {}).get("allow_direct", False)
    if not isinstance(allow_direct, bool):
        raise SettingsError("[main_branch] allow_direct must be true or false")
    return Settings(description=description, allow_direct=allow_direct)


def run(args: Sequence[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), input=stdin, capture_output=True, text=True)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args])


def gh_api(*args: str, stdin: str | None = None) -> Any:
    proc = run([os.environ.get("GH", "gh"), "api", *args], stdin=stdin)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh api {' '.join(args)} failed")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def github_slug(repo: Path) -> tuple[str, str] | None:
    proc = git(repo, "remote", "get-url", "origin")
    match = GITHUB_REMOTE_RE.match(proc.stdout.strip()) if proc.returncode == 0 else None
    return (match["owner"], match["name"]) if match else None


def discover_repos(root: Path) -> list[Path]:
    """Git checkouts one or two levels below the portfolio root."""
    found = {gitdir.parent for pattern in ("*/.git", "*/*/.git") for gitdir in root.glob(pattern)}
    return sorted(r for r in found if "archive-repos" not in r.parts and ".claude" not in r.parts)


def read_settings_text(repo: Path, branch: str, worktree: bool, fetch: bool) -> str:
    if worktree:
        path = repo / SETTINGS_PATH
        if not path.is_file():
            raise SettingsError(f"{SETTINGS_PATH} is missing from the working tree")
        return path.read_text(encoding="utf-8")
    if fetch:
        git(repo, "fetch", "--quiet", "origin", branch)
    proc = git(repo, "show", f"origin/{branch}:{SETTINGS_PATH}")
    if proc.returncode != 0:
        raise SettingsError(f"{SETTINGS_PATH} is missing from origin/{branch}")
    return proc.stdout


def fetch_remote(owner: str, name: str) -> Remote:
    data = gh_api(f"repos/{owner}/{name}")
    return Remote(
        owner=owner,
        name=name,
        visibility=data["visibility"],
        archived=bool(data["archived"]),
        default_branch=data["default_branch"],
        description=data.get("description") or "",
    )


def guard_ruleset_id(remote: Remote) -> int | None:
    for ruleset in gh_api(f"repos/{remote.slug}/rulesets") or []:
        if ruleset.get("name") == RULESET_NAME:
            return int(ruleset["id"])
    return None


def ruleset_matches(remote: Remote, ruleset_id: int) -> bool:
    current = gh_api(f"repos/{remote.slug}/rulesets/{ruleset_id}")
    wanted = ruleset_payload()
    return (
        current.get("enforcement") == wanted["enforcement"]
        and not current.get("bypass_actors")
        and current.get("conditions") == wanted["conditions"]
        and sorted(rule["type"] for rule in current.get("rules", []))
        == sorted(rule["type"] for rule in wanted["rules"])
    )


def plan_repo(repo: Path, args: argparse.Namespace) -> tuple[str, list[str], list[tuple[str, Any]]]:
    """Return (label, findings, actions) for one checkout."""
    slug = github_slug(repo)
    if slug is None:
        return str(repo), [], []
    remote = fetch_remote(*slug)
    label = remote.slug
    if remote.archived:
        return label, [], []
    try:
        settings = parse_settings(
            read_settings_text(repo, remote.default_branch, args.worktree, not args.no_fetch)
        )
    except SettingsError as exc:
        return label, [str(exc)], []

    findings: list[str] = []
    actions: list[tuple[str, Any]] = []
    if remote.description != settings.description:
        findings.append(f"description differs: GitHub has {remote.description!r}")
        actions.append(("description", settings.description))

    if remote.visibility == "public":
        ruleset_id = guard_ruleset_id(remote)
        if settings.allow_direct and ruleset_id is not None:
            findings.append(f"{RULESET_NAME} ruleset present but allow_direct = true")
            actions.append(("delete-ruleset", ruleset_id))
        elif not settings.allow_direct and ruleset_id is None:
            findings.append(f"{RULESET_NAME} ruleset missing")
            actions.append(("create-ruleset", None))
        elif not settings.allow_direct and not ruleset_matches(remote, ruleset_id):
            findings.append(f"{RULESET_NAME} ruleset differs from policy")
            actions.append(("update-ruleset", ruleset_id))
    return label, findings, actions


def apply_action(slug: str, action: str, value: Any) -> None:
    if action == "description":
        gh_api("-X", "PATCH", f"repos/{slug}", "-f", f"description={value}")
    elif action == "create-ruleset":
        gh_api("-X", "POST", f"repos/{slug}/rulesets", "--input", "-", stdin=json.dumps(ruleset_payload()))
    elif action == "update-ruleset":
        gh_api("-X", "PUT", f"repos/{slug}/rulesets/{value}", "--input", "-", stdin=json.dumps(ruleset_payload()))
    elif action == "delete-ruleset":
        gh_api("-X", "DELETE", f"repos/{slug}/rulesets/{value}")
    else:
        raise ValueError(action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "apply"))
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repo", type=Path, action="append", help="Limit to this checkout (repeatable).")
    scope.add_argument(
        "--portfolio-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Scan every checkout under this root (default: ../.. from this repo).",
    )
    parser.add_argument("--worktree", action="store_true", help="Read settings from the working tree.")
    parser.add_argument("--no-fetch", action="store_true", help="Use the existing origin/<branch> ref.")
    parser.add_argument("--dry-run", action="store_true", help="With apply: print changes only.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repos = [r.resolve() for r in args.repo] if args.repo else discover_repos(args.portfolio_root.resolve())
    failed = False
    for repo in repos:
        try:
            label, findings, actions = plan_repo(repo, args)
        except RuntimeError as exc:
            print(f"ERROR {repo.name}: {exc}", file=sys.stderr)
            failed = True
            continue
        if args.command == "check":
            for finding in findings:
                print(f"DRIFT {label}: {finding}")
            failed |= bool(findings)
            continue
        unresolved = [f for f in findings if not actions]
        for finding in unresolved:
            print(f"SKIP  {label}: {finding}")
            failed = True
        for action, value in actions:
            print(f"{'PLAN ' if args.dry_run else 'APPLY'} {label}: {action}")
            if not args.dry_run:
                try:
                    apply_action(label, action, value)
                except RuntimeError as exc:
                    print(f"ERROR {label}: {action}: {exc}", file=sys.stderr)
                    failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
