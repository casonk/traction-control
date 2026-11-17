#!/usr/bin/env python3
"""Choose which self-hosted runner pool each private repository's CI uses.

A private repository sends its CI to a self-hosted scale set through the repository
variable ``CI_RUNS_ON`` (see the ``casonk/.github`` README). A scale set is named
after the pool it lives in:

- ``local``: ``<repo>-ci``, on the local K3s node;
- ``azure``: ``<repo>-azure``, on the Azure VM;
- ``hosted``: no variable, so CI uses GitHub-hosted runners (which cost Actions minutes).

Only private repositories are ever touched: self-hosted runners must not run code
from a public repository. ``set azure`` refuses a repository that has no recorded
Azure scale set (``enroll``), because CI would otherwise queue forever waiting for a
runner that does not exist; ``--force`` overrides that.

Commands:

- ``status``: the pool each private repository points at.
- ``set POOL``: point repositories at a pool (``--repo`` limits it, ``--dry-run`` previews).
- ``balance``: spread repositories over pools by weight (``--weights azure=2,local=1``).
- ``enroll POOL`` / ``unenroll POOL``: record which repositories have a scale set in a pool.
- ``private``: print the private repository slugs (used by the installers).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repository_visibility as visibility  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = REPO_ROOT / "config" / "repository-visibility"
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
DEFAULT_STATE_DIR = STATE_HOME / "portfolio-runner-pool"
VARIABLE = "CI_RUNS_ON"
POOLS = {"local": "{name}-ci", "azure": "{name}-azure"}
HOSTED = "hosted"
CHOICES = (*POOLS, HOSTED)


class PoolError(Exception):
    """A failure that ends the command with a message, not a traceback."""


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def run(command: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)


def short_name(slug: str) -> str:
    return slug.rsplit("/", 1)[-1]


def scale_set_name(pool: str, slug: str) -> str:
    return POOLS[pool].format(name=short_name(slug).lower().replace("_", "-").replace(".", "-"))


def pool_of(slug: str, value: str | None) -> str:
    """Name the pool a variable value belongs to."""
    if not value:
        return HOSTED
    for pool in POOLS:
        if value == scale_set_name(pool, slug):
            return pool
    return "other"


def private_slugs(private: Path, public: Path, local_private: Path | None = None) -> list[str]:
    try:
        pair = visibility.load_pair(private, public, local_private)
    except (visibility.RegistryError, OSError) as exc:
        raise PoolError(f"cannot read the visibility registry: {exc}") from exc
    return sorted({e.slug for e in pair.private.repositories}, key=str.casefold)


def select(universe: Sequence[str], wanted: Sequence[str]) -> list[str]:
    if not wanted:
        return list(universe)
    by_key = {s.casefold(): s for s in universe}
    by_name = {short_name(s).casefold(): s for s in universe}
    chosen, unknown = [], []
    for item in wanted:
        slug = by_key.get(item.casefold()) or by_name.get(item.casefold())
        (chosen if slug else unknown).append(slug or item)
    if unknown:
        raise PoolError(f"not a private repository in the registry: {', '.join(unknown)}")
    return list(dict.fromkeys(chosen))


# --- recorded scale sets ----------------------------------------------------


def enrolled_file(state_dir: Path, pool: str) -> Path:
    return state_dir / f"{pool}-enrolled.json"


def load_enrolled(state_dir: Path, pool: str) -> set[str]:
    try:
        data = json.loads(enrolled_file(state_dir, pool).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError as exc:
        raise PoolError(f"{enrolled_file(state_dir, pool)} is not valid JSON: {exc}") from exc
    return {str(item) for item in data.get("repositories", [])}


def save_enrolled(state_dir: Path, pool: str, slugs: set[str]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = enrolled_file(state_dir, pool)
    path.write_text(json.dumps({"repositories": sorted(slugs, key=str.casefold)}, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


# --- GitHub -----------------------------------------------------------------


def get_variable(slug: str, runner: Runner = run) -> str | None:
    result = runner(["gh", "api", f"repos/{slug}/actions/variables/{VARIABLE}", "--jq", ".value"])
    if result.returncode != 0:
        if "404" in (result.stderr + result.stdout) or "Not Found" in (result.stderr + result.stdout):
            return None
        raise PoolError(f"{slug}: cannot read {VARIABLE}: {(result.stderr or result.stdout).strip()[:120]}")
    return result.stdout.strip() or None


def set_variable(slug: str, value: str | None, runner: Runner = run) -> None:
    if value is None:
        result = runner(["gh", "variable", "delete", VARIABLE, "--repo", slug])
        text = result.stderr + result.stdout
        if result.returncode != 0 and "404" not in text and "Not Found" not in text:
            raise PoolError(f"{slug}: cannot delete {VARIABLE}: {text.strip()[:120]}")
        return
    result = runner(["gh", "variable", "set", VARIABLE, "--repo", slug, "--body", value])
    if result.returncode != 0:
        raise PoolError(f"{slug}: cannot set {VARIABLE}: {(result.stderr or result.stdout).strip()[:120]}")


# --- commands ---------------------------------------------------------------


def status(slugs: Sequence[str], runner: Runner = run) -> list[tuple[str, str, str | None]]:
    rows = []
    for slug in slugs:
        value = get_variable(slug, runner)
        rows.append((slug, pool_of(slug, value), value))
    return rows


def plan_set(
    slugs: Sequence[str], pool: str, state_dir: Path, *, force: bool = False
) -> list[tuple[str, str | None]]:
    """Return (slug, new value) pairs; raise when a pool has no scale set for a repository."""
    if pool not in CHOICES:
        raise PoolError(f"unknown pool {pool!r}; choose from {', '.join(CHOICES)}")
    if pool == "azure" and not force:
        enrolled = load_enrolled(state_dir, "azure")
        missing = [s for s in slugs if s not in enrolled]
        if missing:
            raise PoolError(
                "no recorded Azure scale set for: " + ", ".join(short_name(s) for s in missing)
                + ". Install the scale sets (scripts/install_azure_runner_scale_sets.sh), "
                "or pass --force to point them there anyway."
            )
    return [(slug, None if pool == HOSTED else scale_set_name(pool, slug)) for slug in slugs]


def parse_weights(text: str) -> dict[str, int]:
    weights: dict[str, int] = {}
    for part in text.split(","):
        pool, _, number = part.partition("=")
        pool = pool.strip()
        if pool not in POOLS or not number.strip().isdigit():
            raise PoolError(f"bad weight {part!r}; use e.g. azure=2,local=1 with pools {', '.join(POOLS)}")
        weights[pool] = int(number)
    if not weights or sum(weights.values()) == 0:
        raise PoolError("weights must add up to more than zero")
    return weights


def assign(slugs: Sequence[str], weights: dict[str, int]) -> dict[str, str]:
    """Spread repositories over pools in proportion to the weights, the same way every run."""
    ring = [pool for pool, weight in sorted(weights.items()) for _ in range(weight)]
    ordered = sorted(slugs, key=lambda s: hashlib.sha256(s.casefold().encode()).hexdigest())
    return {slug: ring[index % len(ring)] for index, slug in enumerate(ordered)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, default=REGISTRY_DIR / "private.local.json")
    parser.add_argument("--public", type=Path, default=REGISTRY_DIR / "public.local.json")
    parser.add_argument("--local-private", type=Path, default=REGISTRY_DIR / "local-private.local.json")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    def repos(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", action="append", default=[], metavar="SLUG", help="Limit to a repository (repeatable).")

    sub.add_parser("private", help="Print the private repository slugs.")
    st = sub.add_parser("status", help="Show each private repository's pool.")
    repos(st)
    st.add_argument("--json", action="store_true")
    s = sub.add_parser("set", help="Point repositories at a pool.")
    s.add_argument("pool", choices=CHOICES)
    repos(s)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true", help="Skip the check that a scale set is recorded.")
    b = sub.add_parser("balance", help="Spread repositories over pools by weight.")
    b.add_argument("--weights", required=True, help="e.g. azure=2,local=1")
    repos(b)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--force", action="store_true")
    for name in ("enroll", "unenroll"):
        e = sub.add_parser(name, help=f"{name.capitalize()} repositories in a pool's recorded scale sets.")
        e.add_argument("pool", choices=tuple(POOLS))
        repos(e)
    return parser


def main(argv: Sequence[str] | None = None, runner: Runner = run) -> int:
    args = _parser().parse_args(argv)
    try:
        universe = private_slugs(args.private, args.public, args.local_private)
        if args.command == "private":
            print("\n".join(universe))
            return 0
        slugs = select(universe, args.repo)

        if args.command == "status":
            rows = status(slugs, runner)
            if args.json:
                print(json.dumps([{"repository": s, "pool": p, "value": v} for s, p, v in rows], indent=2))
            else:
                for slug, pool, value in rows:
                    print(f"{short_name(slug):<24} {pool:<7} {value or '-'}")
                counts: dict[str, int] = {}
                for _, pool, _ in rows:
                    counts[pool] = counts.get(pool, 0) + 1
                print("pools: " + ", ".join(f"{p}={n}" for p, n in sorted(counts.items())))
            return 0

        if args.command in ("enroll", "unenroll"):
            current = load_enrolled(args.state_dir, args.pool)
            current = current | set(slugs) if args.command == "enroll" else current - set(slugs)
            save_enrolled(args.state_dir, args.pool, current)
            print(f"{args.pool}: {len(current)} repositories recorded")
            return 0

        if args.command == "set":
            changes = plan_set(slugs, args.pool, args.state_dir, force=args.force)
        else:  # balance
            assignment = assign(slugs, parse_weights(args.weights))
            changes = []
            for pool in sorted(set(assignment.values())):
                members = [s for s, p in assignment.items() if p == pool]
                changes += plan_set(sorted(members), pool, args.state_dir, force=args.force)

        failures = 0
        for slug, value in changes:
            label = value or "(hosted: variable removed)"
            if args.dry_run:
                print(f"would set {short_name(slug)} -> {label}")
                continue
            try:
                set_variable(slug, value, runner)
                print(f"{short_name(slug)} -> {label}")
            except PoolError as exc:
                failures += 1
                print(f"error: {exc}", file=sys.stderr)
        return 1 if failures else 0
    except PoolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
