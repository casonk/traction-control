#!/usr/bin/env python3
"""Classify local branches by their real relationship to ``origin/main``.

Motivation: the obvious tests are wrong. ``git cherry origin/main <branch>``
reports every commit of a *squash-merged* branch as unmerged, because the
squash changes patch-ids. Comparing ``<branch>:file`` against ``origin/main:file``
reports a *merged-then-superseded* branch as unmerged, because ``main`` advanced
the file after the merge. Both produce false "unmerged" verdicts and an
inflated backlog of branches that look like they need PRs but do not.

This tool uses two reliable signals instead:

1. **PR status** (authoritative when present): a branch with a merged PR is
   merged; an open PR means it is already tracked; a closed-unmerged PR means
   someone decided not to merge it — reopening it is a decision, not a cleanup.

2. **``git merge-tree --write-tree origin/main <branch>``** (git >= 2.38): a
   real three-way merge with no working tree. If it succeeds and the resulting
   tree equals ``origin/main``'s tree, merging the branch changes nothing — the
   content is already in main (squash leftover or behind-main). If it reports
   conflicts, the branch has diverged from main (typically superseded work) and
   is not a clean PR. Only a clean, non-empty merge tree with no existing PR is
   a branch that genuinely warrants a new PR.

Categories:

- ``merged``       - fully in main's history, or has a merged PR.
- ``open-pr``      - an open PR already exists.
- ``closed-pr``    - a closed (unmerged) PR exists; not a cleanup target.
- ``redundant``    - no PR; merging is a no-op (content already in main).
- ``diverged``     - no PR; merging conflicts (superseded / needs manual review).
- ``needs-pr``     - no PR; merges cleanly and adds content. The actionable set.

Only ``needs-pr`` should ever become a new PR automatically. ``redundant`` is
safe to delete. ``diverged`` and ``closed-pr`` need a human.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

CATEGORY_ORDER = [
    "needs-pr",
    "diverged",
    "open-pr",
    "closed-pr",
    "redundant",
    "merged",
    "error",
]


def git(repo: Path, *args: str) -> tuple[int, str]:
    """Run a git command in ``repo``; return (exit code, stripped stdout)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout.strip()


def local_branches(repo: Path) -> list[str]:
    """Local branch names except ``main``."""
    _, out = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return [b for b in out.splitlines() if b and b != "main"]


def current_branch(repo: Path) -> str:
    _, out = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out


def pr_state(repo: Path, branch: str, use_gh: bool) -> str | None:
    """Most relevant PR state for ``branch``: merged > open > closed, or None."""
    if not use_gh:
        return None
    proc = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "state"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        states = {row.get("state", "").upper() for row in json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return None
    for want in ("MERGED", "OPEN", "CLOSED"):
        if want in states:
            return want.lower()
    return None


def classify(repo: Path, branch: str, use_gh: bool) -> tuple[str, str]:
    """Return (category, one-line detail) for ``branch`` vs origin/main."""
    rc, _ = git(repo, "rev-parse", "--verify", "--quiet", "origin/main")
    if rc != 0:
        return "error", "no origin/main"

    _, ahead = git(repo, "rev-list", "--count", f"origin/main..{branch}")
    if ahead == "0":
        return "merged", "already in main's history"

    pr = pr_state(repo, branch, use_gh)
    if pr == "merged":
        return "merged", f"merged PR (ahead={ahead}, main advanced)"
    if pr == "open":
        return "open-pr", f"open PR (ahead={ahead})"

    # git merge-tree: does merging this branch into main change anything?
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", "origin/main", branch],
        capture_output=True,
        text=True,
    )
    _, main_tree = git(repo, "rev-parse", "origin/main^{tree}")
    if proc.returncode != 0:
        # Non-zero => merge conflicts => diverged from main.
        if pr == "closed":
            return "closed-pr", f"closed PR, and conflicts with main (ahead={ahead})"
        return "diverged", f"merging conflicts with main (ahead={ahead}, superseded?)"

    merged_tree = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if merged_tree == main_tree:
        return "redundant", f"merge is a no-op; content already in main (ahead={ahead})"
    if pr == "closed":
        return "closed-pr", f"closed PR; would still add content (ahead={ahead})"
    return "needs-pr", f"clean merge adds content (ahead={ahead})"


def discover_repos(root: Path) -> list[Path]:
    """Every git repo under ``root`` (by .git dir), skipping archives/worktrees."""
    repos = []
    for gitdir in root.glob("*/*/.git"):
        repos.append(gitdir.parent)
    for gitdir in root.glob("*/.git"):
        repos.append(gitdir.parent)
    out = []
    for r in sorted(set(repos)):
        parts = r.parts
        if "archive-repos" in parts or ".claude" in parts:
            continue
        out.append(r)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--repo", type=Path, help="Classify one repository.")
    g.add_argument(
        "--portfolio-root",
        type=Path,
        help="Classify every repo under this root (by .git).",
    )
    p.add_argument("--detail", action="store_true", help="Print every branch, not just totals.")
    p.add_argument(
        "--no-gh",
        action="store_true",
        help="Skip PR lookups (git signals only; faster, less precise).",
    )
    p.add_argument(
        "--needs-pr-only",
        action="store_true",
        help="Print only branches that genuinely warrant a new PR.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repo:
        repos = [args.repo.resolve()]
    else:
        root = (args.portfolio_root or Path.home() / "dev").resolve()
        repos = discover_repos(root)

    use_gh = not args.no_gh
    totals: Counter[str] = Counter()
    rows: list[tuple[str, str, str, str]] = []  # repo, branch, category, detail

    for repo in repos:
        cur = current_branch(repo)
        for b in local_branches(repo):
            cat, detail = classify(repo, b, use_gh)
            if b == cur:
                detail += " [current checkout]"
            totals[cat] += 1
            rows.append((repo.name, b, cat, detail))

    if args.needs_pr_only:
        for repo, b, cat, detail in rows:
            if cat == "needs-pr":
                print(f"{repo:20} {b:40} {detail}")
        n = totals.get("needs-pr", 0)
        print(f"\n{n} branch(es) genuinely warrant a new PR.")
        return 1 if n else 0

    if args.detail:
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        for repo, b, cat, detail in sorted(rows, key=lambda r: (order.get(r[2], 99), r[0], r[1])):
            print(f"{cat:11} {repo:20} {b:40} {detail}")
        print()

    print("branch inventory vs origin/main:")
    width = max((len(c) for c in totals), default=0)
    for cat in CATEGORY_ORDER:
        if totals.get(cat):
            print(f"  {cat:<{width}}  {totals[cat]}")
    print(f"  {'TOTAL':<{width}}  {sum(totals.values())} non-main branches")
    # Exit non-zero if anything genuinely needs a PR, for CI/automation use.
    return 1 if totals.get("needs-pr") else 0


if __name__ == "__main__":
    raise SystemExit(main())
