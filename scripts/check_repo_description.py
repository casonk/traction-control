#!/usr/bin/env python3
"""Check a repository's GitHub description for low-information wording.

The description lives in ``.github/repository.toml`` and is applied to GitHub by
``repo_settings.py``. This checker is deliberately conservative: it flags only
patterns that are wrong on their face, never a harmless wording preference, and
it does not try to judge whether the text still matches the repository. That
semantic comparison against the README and AGENTS.md scope belongs to the
agentic REFS audit (``config/prompts/refs-audit-agentic.md``), which reports
rather than edits.

Errors (exit 1, counted as gaps):

* the description is empty;
* it opens with filler such as "Repository for ..." or "Utility repo ...", which
  tells a reader nothing the repository list does not already say;
* it is scaffold text, meaning several sentences that each begin "Store ...".

Warnings (exit 0, advisory):

* it opens with the repository's own name followed by "is";
* it describes a template or fork rather than this repository;
* it is longer than one comfortable line.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

MAX_LENGTH = 200
FILLER_OPENER_RE = re.compile(
    r"^(?:an?\s+|the\s+|my\s+)?(?:utility|utilities|repo|repository|project|tool|tools)"
    r"(?:\s+repo(?:sitory)?)?\s+(?:for|to|of|that|which)\b",
    re.IGNORECASE,
)
BARE_FILLER_RE = re.compile(
    r"^(?:an?\s+|the\s+)?(?:utility|util)\s+repo(?:sitory)?\b", re.IGNORECASE
)
TEMPLATE_RE = re.compile(r"\b(?:template|forked from|fork of)\b", re.IGNORECASE)


def read_description(settings: Path) -> str | None:
    """The description, or None when the file or key is absent."""
    try:
        data = tomllib.loads(settings.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("repository", {}).get("description")
    return value if isinstance(value, str) else None


def check_description(description: str, repo_name: str) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one description."""
    text = description.strip()
    if not text:
        return ["description is empty"], []

    errors: list[str] = []
    warnings: list[str] = []
    if FILLER_OPENER_RE.match(text) or BARE_FILLER_RE.match(text):
        errors.append(f"description opens with filler wording: {text[:60]!r}")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) >= 2 and sum(s.lower().startswith("store ") for s in sentences) >= 2:
        errors.append("description reads as scaffold text (several sentences beginning 'Store')")
    if re.match(rf"^{re.escape(repo_name)}\s+is\b", text, re.IGNORECASE):
        warnings.append("description begins with the repository's own name; lead with what it does")
    if TEMPLATE_RE.search(text):
        warnings.append("description mentions a template or fork; confirm it still describes this repo")
    if len(text) > MAX_LENGTH:
        warnings.append(f"description is {len(text)} characters; keep it to one line (<= {MAX_LENGTH})")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True, help="repository checkout")
    parser.add_argument("--repo-rel", default=None, help="name to report (default: directory name)")
    args = parser.parse_args(argv)

    settings = args.repo / ".github" / "repository.toml"
    description = read_description(settings)
    if description is None:
        # A missing settings file is repo_settings.py's finding, not this checker's.
        return 0

    name = args.repo.name
    errors, warnings = check_description(description, name)
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(message)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
