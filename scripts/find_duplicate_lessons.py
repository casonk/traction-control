#!/usr/bin/env python3
"""Find lessons duplicated across sibling repos' LESSONSLEARNED.md files.

Cross-repo duplication is the cheapest available signal that a lesson is
portfolio-general rather than repo-specific: when the same rule is written out
in two repos, neither owns it and the copies drift independently. Those are the
candidates to up-integrate into `traction-control/LESSONSLEARNED.md`.

The shared template header bullets are duplicated by design, so they are
excluded rather than reported every run.

Exit code 0 = no duplicates outside the template; 1 = duplicates found.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path


# Bullets every repo inherits from docs/templates/LESSONSLEARNED.md. These are
# duplicated on purpose and are not up-integration candidates.
TEMPLATE_BULLET_PREFIXES = (
    "read this file",
    "read after",
    "add lessons that generalize",
    "add concise, action-oriented lessons",
    "keep entries concise",
    "do not use this file",
    "keep transient status",
    "run the lesson-capture gate",
    "before final reporting",
    "document the repository around its real execution",
    "keep local-only, private, reference-only",
    "keep tracked examples, fixtures",
    "re-run repo-appropriate validation",
    "if the repo exposes a dashboard",
)

# Compared on a normalized prefix: repos wrap at different widths, so full-text
# equality misses copies that are identical in substance.
COMPARE_CHARS = 90


def normalize(bullet: str) -> str:
    """Collapse a lesson bullet to a stable comparison key."""
    text = re.sub(r"^[-*]\s+", "", bullet.strip())
    text = re.sub(r"\s+", " ", text)
    return text.lower()[:COMPARE_CHARS]


def is_template_bullet(key: str) -> bool:
    """Return True for bullets inherited from the shared template."""
    return any(key.startswith(prefix) for prefix in TEMPLATE_BULLET_PREFIXES)


def collect(portfolio_root: Path, exclude: str) -> dict[str, set[str]]:
    """Map each normalized lesson to the set of repos that carry it."""
    index: dict[str, set[str]] = defaultdict(set)
    for lessons in sorted(portfolio_root.glob("*/*/LESSONSLEARNED.md")):
        repo = lessons.parent.name
        if repo == exclude:
            continue
        # Only real repositories. Rendered tier-overlay preview bundles copy a
        # repo's lessons verbatim and would otherwise register as duplicates of
        # their own source.
        if not (lessons.parent / ".git").exists():
            continue
        try:
            text = lessons.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith("- "):
                continue
            key = normalize(line)
            if len(key) < 40 or is_template_bullet(key):
                continue
            index[key].add(repo)
    return index


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Find lessons duplicated across repos, as up-integration candidates."
    )
    parser.add_argument(
        "--portfolio-root",
        default=str(Path(__file__).resolve().parents[3]),
        help="Portfolio root containing util-repos/ and sec-repos/.",
    )
    parser.add_argument(
        "--exclude",
        default="traction-control",
        help="Repo to treat as the control plane and skip.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    index = collect(Path(args.portfolio_root).resolve(), args.exclude)

    duplicates = {k: v for k, v in index.items() if len(v) > 1}
    if not duplicates:
        print("no cross-repo duplicate lessons found")
        return 0

    print(f"{len(duplicates)} lesson(s) duplicated across repos — up-integration candidates:\n")
    for key, repos in sorted(duplicates.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  [{', '.join(sorted(repos))}]")
        print(f"    {key}...\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
