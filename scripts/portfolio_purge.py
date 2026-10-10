#!/usr/bin/env python3
"""Remove stale local state across the portfolio: worktrees, branches, images.

Finished work leaves residue that nothing else collects. Every merged PR leaves
a local branch behind; every agent session leaves a worktree; every container
build leaves dangling layers. None of it is wrong on the day it is created, and
none of it is ever revisited, so it accumulates until a manual sweep finds a
hundred branches and sixty worktrees and has to work out, one by one, which of
them still matter.

This tool does that sweep on a schedule, and it is deliberately conservative.
It only ever removes things that can be shown to hold nothing:

* a **branch** goes only when ``branch_inventory.classify`` calls it ``merged``
  or ``redundant`` -- a merged PR, or a three-way merge into ``origin/main``
  that changes nothing. ``needs-pr``, ``open-pr``, ``closed-pr`` and
  ``diverged`` branches are never touched: each of those is a decision.
* a **worktree** goes only when it is clean, unlocked, older than
  ``--min-age-days``, and sitting on a branch that qualifies above. The age
  guard matters: a worktree created a minute ago from ``main`` is clean and
  "merged" too, and is somebody's work about to start.
* an **orphaned worktree** -- a directory under ``.claude/worktrees`` that git
  no longer lists, left behind when its repository was moved -- is never
  deleted, only reported. Its git link is dead, so git itself cannot vouch for
  it; the report says whether every file in it is ignored or already stored in
  the repository's history, and leaves the removal to a person.
* a **remote branch** (opt-in) goes only when its PR is merged or it has no
  commits that ``origin/main`` lacks.
* **container images** (opt-in) are pruned with ``podman image prune``, which
  removes dangling layers only -- never a tagged image or anything in use.

Nothing is changed without ``--apply``. The default run prints what it would do.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from branch_inventory import classify, discover_repos, git  # noqa: E402

# Categories from branch_inventory that are safe to delete without a human.
REMOVABLE = frozenset({"merged", "redundant"})
SECONDS_PER_DAY = 86400


@dataclass
class Worktree:
    path: Path
    branch: str | None = None
    locked: bool = False
    prunable: bool = False


@dataclass
class Report:
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Things this tool will not act on but a person should look at. Printed on
    # every run, not only with --verbose, because nothing else will raise them.
    attention: list[str] = field(default_factory=list)

    def merge(self, other: Report) -> None:
        self.removed += other.removed
        self.kept += other.kept
        self.errors += other.errors
        self.attention += other.attention


def list_worktrees(repo: Path) -> list[Worktree]:
    """Linked worktrees of ``repo``, excluding the main checkout."""
    _, out = git(repo, "worktree", "list", "--porcelain")
    trees: list[Worktree] = []
    current: Worktree | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = Worktree(path=Path(line[len("worktree ") :]))
            trees.append(current)
        elif current is None:
            continue
        elif line.startswith("branch "):
            current.branch = line[len("branch ") :].removeprefix("refs/heads/")
        elif line.startswith("locked"):
            current.locked = True
        elif line.startswith("prunable"):
            current.prunable = True
    return trees[1:]


def is_clean(worktree: Path) -> bool:
    """True when the worktree has no staged, unstaged or untracked changes."""
    # --no-optional-locks: a plain `git status` refreshes and rewrites the
    # index, which would make the act of inspecting a worktree modify it.
    proc = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and not proc.stdout.strip()


def age_days(worktree: Path, now: float) -> float:
    """Days since the worktree was created or last moved to a new commit.

    Uses the newest of the directory, its HEAD and its HEAD reflog, so a
    worktree checked out long ago but committed to this morning counts as new.
    The index is deliberately not consulted: reading status can rewrite it, so
    its timestamp records when a tool last looked, not when work last happened.
    Uncommitted work needs no age signal -- a dirty worktree is kept regardless.
    """
    newest = 0.0
    _, gitdir = git(worktree, "rev-parse", "--absolute-git-dir")
    candidates = [worktree]
    if gitdir:
        candidates += [Path(gitdir) / "HEAD", Path(gitdir) / "logs" / "HEAD"]
    for candidate in candidates:
        try:
            newest = max(newest, candidate.stat().st_mtime)
        except OSError:
            continue
    return (now - newest) / SECONDS_PER_DAY if newest else 0.0


def run(repo: Path, *args: str) -> tuple[int, str]:
    """Run git and return (exit code, combined output) for error reporting."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def purge_worktrees(
    repo: Path, *, apply: bool, use_gh: bool, min_age_days: float, now: float
) -> tuple[Report, set[str]]:
    """Remove stale worktrees. Returns the report and branches still in use."""
    report = Report()
    in_use: set[str] = set()
    for tree in list_worktrees(repo):
        label = f"{repo.name}: worktree {tree.path.name}"
        if tree.prunable or not tree.path.is_dir():
            # The directory is already gone; only git's bookkeeping remains.
            report.removed.append(f"{label} (stale record)")
            continue

        reason = None
        if tree.locked:
            reason = "locked"
        elif not is_clean(tree.path):
            reason = "uncommitted changes"
        elif age_days(tree.path, now) < min_age_days:
            reason = f"newer than {min_age_days:g} days"
        elif tree.branch is None:
            reason = "detached HEAD"
        else:
            category, detail = classify(repo, tree.branch, use_gh)
            if category not in REMOVABLE:
                reason = f"branch is {category}: {detail}"

        if reason:
            report.kept.append(f"{label} ({reason})")
            if tree.branch:
                in_use.add(tree.branch)
            continue

        if apply:
            rc, out = run(repo, "worktree", "remove", str(tree.path))
            if rc != 0:
                report.errors.append(f"{label}: {out}")
                if tree.branch:
                    in_use.add(tree.branch)
                continue
        report.removed.append(label)

    if apply:
        run(repo, "worktree", "prune")
    return report, in_use


# Where agent sessions create their worktrees. Orphans are only looked for
# here: an unregistered directory anywhere else is not this tool's to judge.
WORKTREE_PARENT = Path(".claude") / "worktrees"


def has_broken_gitlink(directory: Path) -> bool:
    """True when ``directory`` is a worktree whose repository link is dead.

    A linked worktree's ``.git`` is a file naming an admin directory inside the
    main repository. Moving the main repository leaves every such file pointing
    at the old path; `git worktree prune` then drops the admin records, and the
    directories remain on disk, unlisted and unusable.
    """
    gitlink = directory / ".git"
    if not gitlink.is_file():
        return False
    try:
        text = gitlink.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if not text.startswith("gitdir:"):
        return False
    return not Path(text[len("gitdir:") :].strip()).exists()


def unsaved_files(repo: Path, directory: Path) -> list[str] | None:
    """Files in an orphan whose content exists nowhere in ``repo``'s history.

    The orphan's own git link is dead, so `git status` cannot answer "is there
    work here?". This answers it from the main repository instead, read-only:
    a file is accounted for if the orphan's ignore rules ignore it, or if its
    exact content is already stored as a blob. Anything else -- an untracked
    file, or a tracked file with edits that were never committed -- is unsaved.

    Returns None if git could not be asked.
    """
    paths = []
    for path in sorted(directory.rglob("*")):
        if path == directory / ".git" or (path.is_dir() and not path.is_symlink()):
            continue
        paths.append(path.relative_to(directory).as_posix())
    if not paths:
        return []

    base = ["git", f"--git-dir={repo / '.git'}", f"--work-tree={directory}"]
    ignored = subprocess.run(
        [*base, "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=directory,
        input="\0".join(paths) + "\0",
        capture_output=True,
        text=True,
    )
    if ignored.returncode not in (0, 1):  # 1 means "nothing matched"
        return None
    skip = {p for p in ignored.stdout.split("\0") if p}
    candidates = [p for p in paths if p not in skip]

    unsaved = [p for p in candidates if (directory / p).is_symlink()]
    regular = [p for p in candidates if not (directory / p).is_symlink()]
    if not regular:
        return unsaved

    # hash-object without -w computes the blob id and writes nothing.
    hashed = subprocess.run(
        [*base, "hash-object", "--stdin-paths"],
        cwd=directory,
        input="\n".join(regular) + "\n",
        capture_output=True,
        text=True,
    )
    hashes = hashed.stdout.split()
    if hashed.returncode != 0 or len(hashes) != len(regular):
        return None
    present = subprocess.run(
        [*base, "cat-file", "--batch-check"],
        input="\n".join(hashes) + "\n",
        capture_output=True,
        text=True,
    )
    lines = present.stdout.splitlines()
    if present.returncode != 0 or len(lines) != len(regular):
        return None
    unsaved += [p for p, line in zip(regular, lines) if line.endswith(" missing")]
    return unsaved


def report_orphans(repo: Path) -> list[str]:
    """Describe worktree directories git no longer knows about. Read-only.

    These are never removed here. A registered worktree can be vouched for by
    git -- clean, merged, unlocked. An orphan cannot: its link is dead, so the
    only evidence is a comparison made from outside, and deleting a directory
    on that basis is a person's call. The report gives them what they need to
    make it.
    """
    findings: list[str] = []
    parent = repo / WORKTREE_PARENT
    if not parent.is_dir():
        return findings
    registered = {tree.path.resolve() for tree in list_worktrees(repo)}
    for directory in sorted(parent.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        if directory.resolve() in registered or not has_broken_gitlink(directory):
            continue
        label = f"{repo.name}: orphaned worktree {directory}"
        unsaved = unsaved_files(repo, directory)
        if unsaved is None:
            findings.append(f"{label} (could not verify its contents)")
        elif unsaved:
            sample = ", ".join(unsaved[:3]) + (" ..." if len(unsaved) > 3 else "")
            findings.append(
                f"{label} ({len(unsaved)} file(s) not saved in git: {sample})"
            )
        else:
            findings.append(
                f"{label} (every file is ignored or already in git history)"
            )
    return findings


def purge_branches(
    repo: Path, *, apply: bool, use_gh: bool, in_use: set[str]
) -> Report:
    """Delete local branches whose content is already in ``origin/main``."""
    report = Report()
    _, current = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _, out = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    for branch in out.splitlines():
        if not branch or branch == "main":
            continue
        label = f"{repo.name}: branch {branch}"
        if branch == current:
            report.kept.append(f"{label} (checked out)")
            continue
        if branch in in_use:
            report.kept.append(f"{label} (held by a kept worktree)")
            continue
        category, detail = classify(repo, branch, use_gh)
        if category not in REMOVABLE:
            report.kept.append(f"{label} ({category}: {detail})")
            continue
        if apply:
            # -D, not -d: a squash-merged branch is never an ancestor of main,
            # so git's own "fully merged" test refuses exactly the branches
            # this tool exists to remove. classify() is the safety check.
            rc, msg = run(repo, "branch", "-D", branch)
            if rc != 0:
                report.errors.append(f"{label}: {msg}")
                continue
        report.removed.append(f"{label} ({category})")
    return report


def remote_pr_state(repo: Path, branch: str) -> str | None:
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
    for want in ("OPEN", "MERGED", "CLOSED"):
        if want in states:
            return want.lower()
    return None


def purge_remote_branches(repo: Path, *, apply: bool, use_gh: bool) -> Report:
    """Delete remote branches that are merged or carry nothing main lacks."""
    report = Report()
    _, out = git(
        repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"
    )
    for ref in out.splitlines():
        branch = ref.removeprefix("origin/")
        if ref in {"origin", "origin/HEAD", "origin/main"} or not branch:
            continue
        label = f"{repo.name}: remote branch {branch}"
        _, ahead = git(repo, "rev-list", "--count", f"origin/main..{ref}")
        state = remote_pr_state(repo, branch) if use_gh else None
        # An open PR wins over everything: the branch is live review state.
        if state == "open":
            report.kept.append(f"{label} (open PR)")
            continue
        if ahead != "0" and state != "merged":
            report.kept.append(f"{label} (ahead={ahead}, pr={state or 'none'})")
            continue
        if apply:
            rc, msg = run(repo, "push", "origin", "--delete", branch)
            if rc != 0:
                report.errors.append(f"{label}: {msg}")
                continue
        why = "merged PR" if state == "merged" else "nothing main lacks"
        report.removed.append(f"{label} ({why})")
    return report


def purge_images(*, apply: bool) -> Report:
    """Prune dangling container layers. Tagged and in-use images are kept."""
    report = Report()
    if shutil.which("podman") is None:
        report.kept.append("images: podman not installed, skipped")
        return report
    proc = subprocess.run(
        ["podman", "images", "--filter", "dangling=true", "--quiet"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # A stopped podman machine is normal on macOS, not a failure.
        report.kept.append("images: podman not reachable, skipped")
        return report
    count = len(proc.stdout.split())
    if count == 0:
        return report
    if apply:
        pruned = subprocess.run(
            ["podman", "image", "prune", "--force"], capture_output=True, text=True
        )
        if pruned.returncode != 0:
            report.errors.append(f"images: {pruned.stderr.strip()}")
            return report
    report.removed.append(f"images: {count} dangling layer(s)")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Nothing is changed without --apply.",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repo", type=Path, help="Purge one repository.")
    scope.add_argument(
        "--portfolio-root",
        type=Path,
        help="Purge every repo under this root (default: ~/dev).",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete. Default is a dry run."
    )
    parser.add_argument(
        "--no-gh",
        action="store_true",
        help="Skip PR lookups; rely on git signals only (finds fewer candidates).",
    )
    parser.add_argument(
        "--no-fetch", action="store_true", help="Do not fetch before classifying."
    )
    parser.add_argument(
        "--min-age-days",
        type=float,
        default=3.0,
        help="Leave worktrees touched more recently than this alone (default: 3).",
    )
    parser.add_argument(
        "--remote-branches",
        action="store_true",
        help="Also delete merged branches on origin.",
    )
    parser.add_argument(
        "--images", action="store_true", help="Also prune dangling podman layers."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="List what was kept, and why."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repo:
        repos = [args.repo.resolve()]
    else:
        repos = discover_repos((args.portfolio_root or Path.home() / "dev").resolve())

    use_gh = not args.no_gh
    now = time.time()
    total = Report()

    for repo in repos:
        if not args.no_fetch:
            # Classification is against origin/main, so a stale remote-tracking
            # ref would make merged work look unmerged. Failure is survivable:
            # it only makes the run more conservative.
            run(repo, "fetch", "--prune", "--quiet", "origin")
        rc, _ = git(repo, "rev-parse", "--verify", "--quiet", "origin/main")
        if rc != 0:
            total.kept.append(f"{repo.name}: no origin/main, skipped")
            continue
        trees, in_use = purge_worktrees(
            repo,
            apply=args.apply,
            use_gh=use_gh,
            min_age_days=args.min_age_days,
            now=now,
        )
        total.merge(trees)
        total.attention += report_orphans(repo)
        total.merge(
            purge_branches(repo, apply=args.apply, use_gh=use_gh, in_use=in_use)
        )
        if args.remote_branches:
            total.merge(
                purge_remote_branches(repo, apply=args.apply, use_gh=use_gh)
            )

    if args.images:
        total.merge(purge_images(apply=args.apply))

    verb = "removed" if args.apply else "would remove"
    for line in total.removed:
        print(f"{verb}: {line}")
    if args.verbose:
        for line in total.kept:
            print(f"kept: {line}")
    for line in total.attention:
        print(f"needs attention: {line}")
    for line in total.errors:
        print(f"error: {line}", file=sys.stderr)

    print(
        f"\n{verb} {len(total.removed)}, kept {len(total.kept)}, "
        f"needs attention {len(total.attention)}, "
        f"errors {len(total.errors)} across {len(repos)} repo(s)"
    )
    if not args.apply and total.removed:
        print("dry run: re-run with --apply to delete")
    return 1 if total.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
