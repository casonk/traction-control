#!/usr/bin/env python3
"""Check AGENTS.md files for the portfolio's shared agent conventions.

Like `check_security_md.py`, this checker is concept-based rather than
template-exact. Repos word their sections differently and that is fine; an
exact-string marker produces false positives on repos that already comply.
(The previous exact-match sudo marker in `portfolio-audit.sh` flagged three
repos that each carried a correct, differently-worded Sudo Boundary section.)

Each repo-level AGENTS.md should:

1. hand elevated commands to the user instead of attempting `sudo`,
2. point back at the control plane for portfolio standards,
3. name the local-only session-memory boundary (`CHATHISTORY.md`) and the
   durable lesson file (`LESSONSLEARNED.md`), and
4. for code repositories, state how to verify CI locally before pushing.

Checks 1-3 apply to every repo. Check 4 is reported only when the repo has a
CI workflow to verify, so docs-only repos are not held to it.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SUDO_HEADING_RE = re.compile(
    r"^##+\s+(sudo boundary|elevated commands|privilege boundary)\b",
    re.IGNORECASE | re.MULTILINE,
)
STANDARDS_HEADING_RE = re.compile(
    (
        r"^##+\s+("
        r"portfolio standards(?: reference)?|portfolio references?|"
        r"shared portfolio references|best current internal references"
        r")\b"
    ),
    re.IGNORECASE | re.MULTILINE,
)
LOCAL_CI_HEADING_RE = re.compile(
    r"^##+\s+(local ci verification|local verification|ci verification)\b",
    re.IGNORECASE | re.MULTILINE,
)

# The sudo boundary is a behavioral rule, not a heading. Accept any phrasing
# that both denies `sudo` to the agent and routes the command to the user.
SUDO_DENIAL_TERMS = (
    "cannot run `sudo`",
    "cannot run sudo",
    "will never be able to run `sudo`",
    "will never be able to run sudo",
    "must not run `sudo`",
    "must not run sudo",
    "no sudo",
    "without elevation",
)
SUDO_HANDOFF_TERMS = (
    "hand the exact",
    "hand exact",
    "hand off",
    "give the user the exact",
    "hand the elevated",
    "to the user",
    "for the user to run",
    "user to run themselves",
)

CONTROL_PLANE_TERMS = (
    "traction-control",
    "control plane",
)


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    """Return True if any candidate term appears in the lowercased text."""
    return any(term in text for term in terms)


def repo_has_ci(repo: Path) -> bool:
    """Return True if the repo ships any GitHub Actions workflow."""
    workflows = repo / ".github" / "workflows"
    if not workflows.is_dir():
        return False
    return any(workflows.glob("*.yml")) or any(workflows.glob("*.yaml"))


def check_agents_md(path: Path, repo: Path) -> list[str]:
    """Return a list of shared-convention gaps for an AGENTS.md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unable to read AGENTS.md: {exc}"]

    lowered = text.lower()
    gaps: list[str] = []

    if not text.strip():
        return ["AGENTS.md is empty"]

    # 1. Sudo boundary. A heading alone is not enough, and neither is a bare
    #    mention of sudo: the rule is deny-plus-handoff.
    has_sudo_heading = bool(SUDO_HEADING_RE.search(text))
    has_sudo_rule = contains_any(lowered, SUDO_DENIAL_TERMS) and contains_any(
        lowered, SUDO_HANDOFF_TERMS
    )
    if not (has_sudo_heading and has_sudo_rule):
        gaps.append(
            "missing a sudo boundary that both denies `sudo` to the agent and "
            "hands the exact elevated command to the user"
        )

    # 2. Portfolio standards backlink.
    if not STANDARDS_HEADING_RE.search(text):
        gaps.append(
            "missing a portfolio standards reference heading such as "
            "`## Portfolio Standards Reference` or `## Portfolio References`"
        )
    elif not contains_any(lowered, CONTROL_PLANE_TERMS):
        gaps.append(
            "portfolio standards section does not point back at the control plane"
        )

    # 3. Session-memory and durable-lesson boundary.
    if "chathistory.md" not in lowered:
        gaps.append("does not name `CHATHISTORY.md` as the local-only session memory")
    if "lessonslearned.md" not in lowered:
        gaps.append("does not name `LESSONSLEARNED.md` as the durable lesson file")

    # 4. Local CI verification, only where there is CI to verify.
    if repo_has_ci(repo) and not LOCAL_CI_HEADING_RE.search(text):
        gaps.append(
            "repo ships CI workflows but AGENTS.md has no `## Local CI Verification` "
            "section describing how to reproduce them locally"
        )

    return gaps


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Check a repo AGENTS.md file for shared portfolio conventions."
    )
    parser.add_argument("--repo", required=True, help="Path to the repository root to inspect.")
    parser.add_argument(
        "--repo-rel",
        default=None,
        help="Optional repo path relative to the portfolio root for display only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    agents_md = repo / "AGENTS.md"

    if not agents_md.exists():
        print("missing AGENTS.md")
        return 1

    gaps = check_agents_md(agents_md, repo)
    for gap in gaps:
        print(gap)
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
