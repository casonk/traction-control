#!/usr/bin/env python3
"""Check SECURITY.md files for portfolio security-policy best practices.

This checker is intentionally concept-based rather than template-exact. Repos
may use different section names, but each SECURITY.md should still:

1. identify itself as a security policy,
2. tell reporters to avoid public disclosure details,
3. provide a private reporting path or private-handling guidance,
4. describe sensitive content or operational boundaries, and
5. avoid copy/paste repo-name mistakes in repo-specific reporting text.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


REPORTING_HEADING_RE = re.compile(
    r"^##+\s+(reporting|reporting a vulnerability|reporting a vuln|disclosure policy)\b",
    re.IGNORECASE | re.MULTILINE,
)
BOUNDARY_HEADING_RE = re.compile(
    (
        r"^##+\s+("
        r"scope|operational boundaries|sensitive data notice|sensitive content|"
        r"handling rules|safe documentation practices|safe development practices|"
        r"safe operating practices|what not to report publicly|hard rules|"
        r"hard rules \u2014 what must never be committed|supported versions"
        r")\b"
    ),
    re.IGNORECASE | re.MULTILINE,
)

PUBLIC_DISCLOSURE_TERMS = (
    "public issue",
    "public issues",
    "public github issue",
    "public github issues",
    "open a public issue",
    "opening a public issue",
    "file sensitive disclosures in public issues",
    "disclose the issue publicly",
)
PRIVATE_REPORTING_TERMS = (
    "private",
    "privately",
    "repository owner",
    "maintainer directly",
    "contact the maintainer",
    "private vulnerability reporting",
    "github security advisory",
    "security advisory",
    "encrypted email",
    "private channel",
)
SENSITIVE_CONTENT_TERMS = (
    "credential",
    "credentials",
    "token",
    "tokens",
    "private key",
    "private keys",
    "password",
    "passwords",
    "secret",
    "secrets",
    "personal data",
    "financial information",
    "pii",
    "host-specific",
    "machine-specific",
    "local-only",
    "local environment",
    "absolute filesystem path",
    "absolute filesystem paths",
    "wallet",
    "wallets",
    "certificate",
    "certificates",
    "localhost",
    "public network interface",
    "private environment",
    "endpoint addresses",
    "unpublished",
)
REPO_REFERENCE_PATTERNS = (
    re.compile(r"For\s+`([^`]+)`", re.IGNORECASE),
    re.compile(
        r"If you discover a security vulnerability in\s+([A-Za-z0-9_.-]+)",
        re.IGNORECASE,
    ),
    re.compile(r"If you discover a security issue in\s+([A-Za-z0-9_.-]+)", re.IGNORECASE),
)


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    """Return True if any candidate term appears in the lowercased text."""
    return any(term in text for term in terms)


def find_repo_reference_mismatches(text: str, repo_name: str) -> list[str]:
    """Flag copy/paste mistakes where the SECURITY.md names a different repo."""
    mismatches: list[str] = []
    seen: set[str] = set()
    for pattern in REPO_REFERENCE_PATTERNS:
        for match in pattern.findall(text):
            candidate = match.strip().strip("`").strip()
            candidate_lower = candidate.lower()
            if candidate_lower in {
                "this",
                "this repository",
                "this project",
                "this tool",
                "repository",
                "project",
                "tool",
            }:
                continue
            if candidate_lower != repo_name.lower() and candidate_lower not in seen:
                mismatches.append(
                    f"repo-specific reporting text names `{candidate}` instead of `{repo_name}`"
                )
                seen.add(candidate_lower)
    return mismatches


def check_security_md(path: Path, repo_name: str) -> list[str]:
    """Return a list of best-practice gaps for a SECURITY.md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unable to read SECURITY.md: {exc}"]

    lowered = text.lower()
    gaps: list[str] = []

    if not text.strip():
        gaps.append("SECURITY.md is empty")
        return gaps

    if len(text.strip()) < 120:
        gaps.append("SECURITY.md is too short to communicate a usable policy")

    if not re.search(r"^#\s+security", text, re.IGNORECASE | re.MULTILINE):
        gaps.append("missing a top-level security-policy heading")

    if not REPORTING_HEADING_RE.search(text):
        gaps.append(
            "missing a reporting/disclosure heading such as `## Reporting` or "
            "`## Reporting a Vulnerability`"
        )

    if not BOUNDARY_HEADING_RE.search(text):
        gaps.append(
            "missing an operational-boundary heading such as `## Scope`, "
            "`## Sensitive Content`, or `## Safe Documentation Practices`"
        )

    if not contains_any(lowered, PUBLIC_DISCLOSURE_TERMS):
        gaps.append("missing guidance to avoid public disclosure details in issues or reports")

    if not contains_any(lowered, PRIVATE_REPORTING_TERMS):
        gaps.append("missing a private reporting path or private-handling instruction")

    if not contains_any(lowered, SENSITIVE_CONTENT_TERMS):
        gaps.append("missing examples of sensitive content or non-public operational data")

    gaps.extend(find_repo_reference_mismatches(text, repo_name))
    return gaps


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Check a repo SECURITY.md file for portfolio best-practice coverage."
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
    security_md = repo / "SECURITY.md"

    if not security_md.exists():
        print("missing SECURITY.md")
        return 1

    gaps = check_security_md(security_md, repo.name)
    if not gaps:
        return 0

    for gap in gaps:
        print(gap)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
