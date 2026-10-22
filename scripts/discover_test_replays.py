#!/usr/bin/env python3
"""Find public PR commits that could become historical test replay cases.

This is discovery only. Candidates must pass crew-chief's historical preflight
before they become benchmark cases. Reports and origin evidence stay ignored.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import portfolio_materializer as materializer
import repository_visibility as visibility


DEFAULT_PRIVATE = Path("config/repository-visibility/private.local.json")
DEFAULT_PUBLIC = Path("config/repository-visibility/public.local.json")
DEFAULT_CATALOG = Path("config/portfolio/portfolio.local.json")
DEFAULT_OUTPUT = Path("reports/test-replay-candidates")
PR_MARKER = re.compile(r"\(#([1-9][0-9]*)\)")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise ValueError(f"git inspection failed for {repo.name}: {' '.join(args[:2])}")
    return result.stdout.strip()


def _changed_paths(repo: Path, before: str, after: str) -> tuple[list[str], list[str]]:
    tests: list[str] = []
    sources: list[str] = []
    for line in git(repo, "diff", "--name-status", before, after, "--").splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or parts[0] not in {"A", "M"}:
            continue
        status, path = parts
        if (
            path.startswith("tests/")
            and Path(path).name.startswith("test_")
            and path.endswith(".py")
        ):
            tests.append(path)
        elif status == "M" and path.startswith("src/") and path.endswith(".py"):
            sources.append(path)
    return sorted(tests), sorted(sources)


def discover(repo: Path, slug: str, max_commits: int) -> list[dict]:
    commits = git(
        repo, "log", "--first-parent", f"--max-count={max_commits}", "--format=%H"
    ).splitlines()
    cases: list[dict] = []
    for after in commits:
        title = git(repo, "show", "-s", "--format=%s", after)
        marker = PR_MARKER.search(title)
        if not marker:
            continue
        parents = git(repo, "rev-list", "--parents", "-n", "1", after).split()
        if len(parents) < 2:
            continue
        before = parents[1]
        tests, sources = _changed_paths(repo, before, after)
        if not tests or not sources:
            continue
        cases.append(
            {
                "id": f"{slug}@{after}",
                "repository": slug,
                "before": before,
                "after": after,
                "pr_hint": int(marker.group(1)),
                "subject": title,
                "test_paths": tests,
                "editable_paths": sources,
                "status": "unreviewed",
            }
        )
    return cases


def _validate_public_checkout(
    repo: Path, root: Path, entry: visibility.RepositoryEntry
) -> None:
    if not repo.resolve().is_relative_to(root.resolve()):
        raise ValueError("catalog checkout escapes portfolio root")
    materializer.verify_checkout(repo, entry, require_clean=False)


def public_checkouts(
    root: Path, pair: visibility.RegistryPair, catalog: materializer.CatalogDocument
) -> list[tuple[str, str, Path]]:
    public = {entry.repository_id: entry for entry in pair.public.repositories}
    checkouts = []
    for entry in catalog.repositories:
        registered = public.get(entry.repository_id)
        if (
            not registered
            or entry.lifecycle != "active"
            or entry.desired_presence != "checkout"
        ):
            continue
        repo = root / entry.relative_path
        if not repo.is_dir() or not (repo / ".git").exists():
            continue
        _validate_public_checkout(repo, root, registered)
        checkouts.append((registered.slug, entry.relative_path, repo))
    return checkouts


def explicit_checkouts(
    root: Path, pair: visibility.RegistryPair, relative_paths: list[str]
) -> list[tuple[str, str, Path]]:
    public = {entry.slug.casefold(): entry for entry in pair.public.repositories}
    checkouts = []
    for relative_path in relative_paths:
        materializer._validate_relative_path(relative_path)
        repo = root / relative_path
        if not repo.is_dir():
            raise ValueError("explicit checkout does not exist")
        remote = git(repo, "remote", "get-url", "origin")
        slug = visibility._normalize_github_remote(remote)
        registered = public.get(slug.casefold()) if slug else None
        if registered is None:
            raise ValueError(
                "explicit checkout is not in the public visibility registry"
            )
        _validate_public_checkout(repo, root, registered)
        checkouts.append((registered.slug, relative_path, repo))
    if len({slug for slug, _, _ in checkouts}) != len(checkouts):
        raise ValueError("duplicate public checkout identity")
    return checkouts


def _write_owner_only(path: Path, payload: dict) -> None:
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, pending_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    pending = Path(pending_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
        os.chmod(pending, 0o600)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _evidence_ledger(path: Path, cases: list[dict]) -> dict:
    if path.exists():
        payload = json.loads(path.read_text())
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("cases"), dict)
        ):
            raise ValueError(
                "existing origin evidence ledger has an unsupported format"
            )
    else:
        payload = {"schema_version": 1, "cases": {}}
    for case in cases:
        payload["cases"].setdefault(
            case["id"],
            {"status": "unknown", "model": None, "evidence": [], "checked_sources": []},
        )
    for record in payload["cases"].values():
        if not isinstance(record, dict) or record.get("status") not in {
            "unknown",
            "verified",
        }:
            raise ValueError("origin evidence record has an unsupported status")
        record.setdefault("checked_sources", [])
        if not isinstance(record.get("evidence"), list) or not isinstance(
            record.get("checked_sources"), list
        ):
            raise ValueError(
                "origin evidence record needs evidence and checked_sources lists"
            )
        if record["status"] == "verified" and (
            not record.get("model") or not record["evidence"]
        ):
            raise ValueError("verified origin model needs a model and direct evidence")
        if record["status"] == "unknown" and record.get("model") is not None:
            raise ValueError("unknown origin model must be null")
    return payload


def write_candidates(
    output: Path, cases: list[dict], locations: dict[str, str]
) -> None:
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.is_symlink() or output.stat().st_mode & 0o077:
        raise ValueError("output directory must be owner-only and not a symlink")
    visibility._require_ignored_or_outside_git(output)
    task_dir = output / "tasks"
    task_dir.mkdir(mode=0o700, exist_ok=True)
    if task_dir.is_symlink() or task_dir.stat().st_mode & 0o077:
        raise ValueError("task directory must be owner-only and not a symlink")
    for case in cases:
        slug = case["repository"]
        filename = f"{slug.replace('/', '--')}--{case['after'][:12]}.json"
        case["task_file"] = f"tasks/{filename}"
        prompt = (
            "Later regression tests were added to an older checkout. Update the existing "
            "implementation to satisfy them and run the full test suite. Edit only: "
            + ", ".join(case["editable_paths"])
            + "."
        )
        _write_owner_only(
            task_dir / filename,
            {
                "name": f"{slug} PR #{case['pr_hint']} ({case['after'][:12]})",
                "prompt": prompt,
                "evolution": {
                    "before": case["before"],
                    "after": case["after"],
                    "test_paths": case["test_paths"],
                },
                "test_command": ["pytest", "-q"],
                "editable_paths": case["editable_paths"],
            },
        )
    _write_owner_only(
        output / "index.json",
        {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "unreviewed_candidates",
            "cases": cases,
            "note": "Test commands and prompts are drafts; run historical preflight before benchmarking.",
        },
    )
    _write_owner_only(
        output / "origin-evidence.local.json",
        _evidence_ledger(output / "origin-evidence.local.json", cases),
    )
    _write_owner_only(
        output / "candidate-suite.json",
        {
            "cases": [
                {"repo": locations[case["repository"]], "task": case["task_file"]}
                for case in cases
            ],
            "status": "unreviewed; remove this field only after preflight",
        },
    )


def review_candidates(
    output: Path,
    root: Path,
    cases: list[dict],
    locations: dict[str, str],
    benchmark_script: Path,
    test_timeout: int,
    limit: int,
) -> dict:
    spec = importlib.util.spec_from_file_location(
        "crew_chief_test_benchmark", benchmark_script
    )
    if spec is None or spec.loader is None:
        raise ValueError("cannot load crew-chief historical benchmark reviewer")
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    results = []
    accepted = []
    with tempfile.TemporaryDirectory(prefix="portfolio-replay-review-") as temporary:
        patch_dir = Path(temporary)
        for case in cases[:limit]:
            repo_path = locations[case["repository"]]
            task_path = output / case["task_file"]
            task = benchmark.load_task(task_path)
            try:
                review = benchmark.prepare_evolution(
                    root / repo_path, task, patch_dir, test_timeout
                )
                if not review["new_tests_on_old_source"]["counts"].get("failed"):
                    raise ValueError(
                        "new tests caused collection errors without an assertion failure"
                    )
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                results.append(
                    {"id": case["id"], "status": "rejected", "reason": str(exc)[-1000:]}
                )
                continue
            results.append({"id": case["id"], "status": "accepted", "review": review})
            accepted.append({"repo": repo_path, "task": case["task_file"]})
    _write_owner_only(
        output / "review.json",
        {
            "schema_version": 1,
            "reviewed": len(results),
            "accepted": len(accepted),
            "results": results,
        },
    )
    _write_owner_only(output / "reviewed-suite.json", {"cases": accepted})
    return {"reviewed": len(results), "accepted": len(accepted)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio-root", type=Path, default=Path("../.."))
    parser.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-commits", type=int, default=40)
    parser.add_argument(
        "--review",
        action="store_true",
        help="Run crew-chief's historical test preflight",
    )
    parser.add_argument("--review-limit", type=int, default=20)
    parser.add_argument("--test-timeout", type=int, default=180)
    parser.add_argument(
        "--benchmark-script",
        type=Path,
        default=Path("../crew-chief/scripts/test_benchmark.py"),
    )
    parser.add_argument(
        "--repo", action="append", help="Restrict to a verified public OWNER/REPO"
    )
    parser.add_argument(
        "--checkout",
        action="append",
        help="Explicit public checkout path relative to portfolio root when no catalog is present",
    )
    args = parser.parse_args()
    if min(args.max_commits, args.review_limit, args.test_timeout) < 1:
        parser.error("max commits, review limit, and test timeout must be positive")
    root = args.portfolio_root.resolve()
    pair = visibility.load_pair(args.private, args.public)
    if args.checkout:
        checkouts = explicit_checkouts(root, pair, args.checkout)
    else:
        if not args.catalog.exists():
            parser.error(
                "portfolio catalog is missing; use --checkout for verified public paths"
            )
        catalog = materializer.load_catalog(args.catalog, pair)
        checkouts = public_checkouts(root, pair, catalog)
    requested = set(args.repo or [])
    available = {slug for slug, _, _ in checkouts}
    if requested - available:
        parser.error("requested repository is not an active verified public checkout")
    cases = []
    locations = {}
    for slug, relative_path, repo in checkouts:
        if requested and slug not in requested:
            continue
        locations[slug] = relative_path
        cases.extend(discover(repo, slug, args.max_commits))
    cases.sort(key=lambda case: (case["repository"], case["after"]))
    write_candidates(args.output_dir.resolve(), cases, locations)
    print(
        f"Scanned {len(locations)} public checkouts; found {len(cases)} unreviewed cases."
    )
    print(f"Candidate index: {args.output_dir.resolve() / 'index.json'}")
    if args.review:
        summary = review_candidates(
            args.output_dir.resolve(),
            root,
            cases,
            locations,
            args.benchmark_script.resolve(),
            args.test_timeout,
            args.review_limit,
        )
        print(f"Reviewed {summary['reviewed']} cases; accepted {summary['accepted']}.")
        print(f"Reviewed suite: {args.output_dir.resolve() / 'reviewed-suite.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
