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
LIST_RE = re.compile(r"^\s*python-versions?:\s*(?P<value>.+?)\s*(?:#.*)?$", re.MULTILINE)
VERSION_RE = re.compile(r"(?<![\d.])3\.\d{1,2}(?![\d.])")
SIBLING_RE = re.compile(r"github\.com[/:]casonk/([\w.-]+?)(?:\.git)?(?=[\s\"'@#)]|$)")
REQUIRES_RE = re.compile(r'^\s*requires-python\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
SKIP_PARTS = {".git", ".venv", "node_modules", "archive-repos", "vendor"}


def hardcoded_lists(repo: Path) -> list[tuple[str, str]]:
    """(workflow file, literal value) for each ``python-versions`` that is not auto/auto-edges."""
    found = []
    workflows = repo / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        for match in LIST_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            value = match.group("value").strip().strip("'\"")
            if value.startswith("["):  # a literal list (python-versions or a matrix python-version)
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


def minor_key(minor: str) -> tuple[int, int]:
    major, _, rest = minor.partition(".")
    return int(major), int(rest)


def literal_versions(repo: Path) -> list[str]:
    """Every literal ``python-version(s)`` minor a workflow names, list or scalar (not auto)."""
    found: list[str] = []
    workflows = repo / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        for match in LIST_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            found.extend(VERSION_RE.findall(match.group("value")))
    return found


def sibling_floor(repo: Path, name: str) -> str | None:
    """Declared ``requires-python`` floor of the checked-out sibling repo *name*, if present."""
    for base in (repo.parent, repo.parent.parent):
        for candidate in (base / name, *sorted(base.glob(f"*/{name}"))):
            if (candidate / "pyproject.toml").is_file() and candidate.resolve() != repo.resolve():
                return declared_floor(candidate)
    return None


def sibling_dependencies(repo: Path) -> list[str]:
    """Names of ``casonk/<name>`` packages the repo installs in CI or from requirements."""
    sources = list((repo / ".github" / "workflows").glob("*.y*ml"))
    sources += [*repo.glob("requirements*.txt"), repo / "pyproject.toml"]
    names: list[str] = []
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names.extend(SIBLING_RE.findall(text))
    return sorted(set(names) - {repo.name, ".github"})


def dependency_floor_problems(repo: Path) -> list[str]:
    """CI or ``requires-python`` below the Python floor of a sibling package the repo installs."""
    problems = []
    tested = literal_versions(repo)
    own_floor = declared_floor(repo)
    for name in sibling_dependencies(repo):
        floor = sibling_floor(repo, name)
        if not floor:
            continue
        too_old = sorted({v for v in tested if minor_key(v) < minor_key(floor)}, key=minor_key)
        if too_old:
            problems.append(
                f"CI tests Python {', '.join(too_old)} but dependency {name} requires >={floor}"
                " (use auto / auto-edges or raise the version)"
            )
        if own_floor and minor_key(own_floor) < minor_key(floor):
            problems.append(
                f"requires-python floor {own_floor} is below dependency {name}'s >={floor}"
            )
    return problems


def check_repo(repo: Path, eol: set[str] | None) -> list[str]:
    problems = [f"{name}: python-versions {value} is hard-coded (use auto / auto-edges)" for name, value in hardcoded_lists(repo)]
    floor = declared_floor(repo)
    if floor and eol and floor in eol:
        problems.append(f"requires-python floor {floor} is end of life (raise it, or test it knowingly)")
    problems += dependency_floor_problems(repo)
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
    repos = [args.repo.resolve()] if args.repo else discover(args.root.resolve())
    reported = 0
    for repo in repos:
        for problem in check_repo(repo, eol):
            reported += 1
            label = repo.name if args.repo else str(repo.relative_to(args.root.resolve()))
            print(f"{label}: {problem}")
    if eol is None:
        print("note: endoflife.date unreachable; end-of-life floors were not checked", file=sys.stderr)
    return 1 if args.strict and reported else 0


if __name__ == "__main__":
    sys.exit(main())
