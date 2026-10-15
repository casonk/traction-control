#!/usr/bin/env python3
"""Check tracked repository structure and generated-artifact boundaries.

Read the Git index, rather than the working tree, so the result matches what
the next commit would contain. The base profile is safe for every portfolio
repo; code-only requirements are explicit because source-file heuristics
misclassify documentation and operations repositories.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


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
LOCAL_FILES = {"CHATHISTORY.md", "REFS-LOCAL.md"}
RUNTIME_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "build",
    "dist",
    "coverage",
    ".tox",
    "reports",
}
RUNTIME_FILES = {".DS_Store", ".env", ".env.local"}
RENDER_SUFFIXES = (".puml.svg", ".puml.png", ".drawio.svg", ".drawio.png")


def index_entries(repo: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--stage", "-z"],
        check=True,
        capture_output=True,
    )
    entries = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, filename = record.split(b"\t", 1)
        mode, _blob, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise ValueError("unresolved merge entries in Git index")
        entries[filename.decode("utf-8", "surrogateescape")] = mode
    return entries


def check(
    entries: dict[str, str], profile: str, forbidden_prefixes: tuple[str, ...] = ()
) -> list[str]:
    paths = set(entries)
    errors = []
    for required in BASE_FILES:
        if required not in paths:
            errors.append(f"required tracked file missing: {required}")
        elif entries[required] not in {"100644", "100755"}:
            errors.append(f"required file must be a regular file: {required}")

    if profile == "code":
        if ".pre-commit-config.yaml" not in paths:
            errors.append("code profile requires .pre-commit-config.yaml")
        if not any(path.startswith("tests/") for path in paths):
            errors.append("code profile requires tracked tests/")
        if not any(path.startswith(".github/workflows/") for path in paths):
            errors.append("code profile requires a tracked CI workflow")

    for path in sorted(paths):
        parts = Path(path).parts
        if path in LOCAL_FILES or path.endswith((".pyc", ".pyo")):
            errors.append(f"local or runtime file is tracked: {path}")
        elif any(part in RUNTIME_DIRS for part in parts) or parts[-1] in RUNTIME_FILES:
            errors.append(f"local or runtime file is tracked: {path}")
        for prefix in forbidden_prefixes:
            if path.startswith(prefix):
                errors.append(f"repository artifact path is tracked: {path}")
        for suffix in RENDER_SUFFIXES:
            if path.endswith(suffix) and path[:-4] not in paths:
                errors.append(f"render has no tracked source: {path}")
                break
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--profile", choices=("base", "code"), default="base")
    parser.add_argument(
        "--forbid-prefix",
        action="append",
        default=[],
        metavar="PATH/",
        help="additional repository-local artifact directory to keep untracked",
    )
    args = parser.parse_args()
    if any(
        not prefix.endswith("/") or prefix.startswith("/") or ".." in Path(prefix).parts
        for prefix in args.forbid_prefix
    ):
        parser.error("--forbid-prefix must be a relative directory ending in /")
    try:
        entries = index_entries(args.repo)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"quality check setup error: {exc}", file=sys.stderr)
        return 2
    errors = check(entries, args.profile, tuple(args.forbid_prefix))
    if not entries:
        errors.insert(
            0, "Git index has no tracked files; stage the repository scaffold"
        )
    for error in errors:
        print(f"quality check: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
