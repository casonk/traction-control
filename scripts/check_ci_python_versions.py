#!/usr/bin/env python3
"""Report CI workflows that hard-code a Python version list, and EOL ``requires-python`` floors.

The portfolio standard (docs/auto-upgrades.md) is that no repository names Python versions in
its workflows: ``python-versions: auto`` (newest three supported CPython releases) and
``auto-edges`` (declared floor + newest) are resolved centrally by casonk/.github. This lists
the repositories that still carry a literal list, and the ones whose declared floor has reached
end of life (raise ``requires-python`` or accept testing an unsupported Python deliberately).

Advisory by default (always exits 0); ``--strict`` exits 1 when anything is reported.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

FEED = "https://endoflife.date/api/python.json"
LIST_RE = re.compile(r"^\s*python-versions:\s*(?P<value>.+?)\s*(?:#.*)?$", re.MULTILINE)
REQUIRES_RE = re.compile(r'^\s*requires-python\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
SKIP_PARTS = {".git", ".venv", "node_modules", "archive-repos", "vendor"}


def hardcoded_lists(repo: Path) -> list[tuple[str, str]]:
    """(workflow file, literal value) for each ``python-versions`` that is not auto/auto-edges."""
    found = []
    workflows = repo / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        for match in LIST_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            value = match.group("value").strip().strip("'\"")
            if value.startswith("["):  # a literal list; prose, auto/auto-edges and expressions are fine
                found.append((path.name, value))
    return found


def declared_floor(repo: Path) -> str | None:
    try:
        text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    match = REQUIRES_RE.search(text)
    floor = re.search(r">=\s*(3\.\d+)", match.group(1)) if match else None
    return floor.group(1) if floor else None


def eol_minors(today: date | None = None, cycles: list[dict] | None = None) -> set[str] | None:
    """Python minors past end of life, or None when the feed cannot be read."""
    now = (today or date.today()).isoformat()
    try:
        if cycles is None:
            request = urllib.request.Request(FEED, headers={"User-Agent": "traction-control"})
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                cycles = json.load(response)
    except Exception:  # pylint: disable=broad-except
        return None
    return {
        str(c["cycle"])
        for c in cycles
        if re.fullmatch(r"3\.\d+", str(c.get("cycle", "")))
        and (c.get("eol") is True or (isinstance(c.get("eol"), str) and c["eol"] <= now))
    }


def discover(root: Path) -> list[Path]:
    repos = []
    for workflows in root.rglob(".github/workflows"):
        repo = workflows.parent.parent
        if not any(part in SKIP_PARTS for part in repo.relative_to(root).parts):
            repos.append(repo)
    return sorted(set(repos))


def check_repo(repo: Path, eol: set[str] | None) -> list[str]:
    problems = [f"{name}: python-versions {value} is hard-coded (use auto / auto-edges)" for name, value in hardcoded_lists(repo)]
    floor = declared_floor(repo)
    if floor and eol and floor in eol:
        problems.append(f"requires-python floor {floor} is end of life (raise it, or test it knowingly)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, help="check one repository")
    parser.add_argument("--root", type=Path, help="scan every repository under this directory")
    parser.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    args = parser.parse_args(argv)
    if not args.repo and not args.root:
        parser.error("give --repo or --root")
    eol = eol_minors()
    repos = [args.repo] if args.repo else discover(args.root)
    reported = 0
    for repo in repos:
        for problem in check_repo(repo, eol):
            reported += 1
            label = repo.name if args.repo else str(repo.relative_to(args.root))
            print(f"{label}: {problem}")
    if eol is None:
        print("note: endoflife.date unreachable; end-of-life floors were not checked", file=sys.stderr)
    return 1 if args.strict and reported else 0


if __name__ == "__main__":
    sys.exit(main())
