#!/usr/bin/env python3
"""Render registered portfolio diagrams in isolated clones and open review PRs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import portfolio_materializer as materializer
import repository_visibility as visibility


BRANCH_PREFIX = "automation/archility-diagrams-"
PR_TITLE = "docs: refresh generated architecture diagrams"
PR_BODY = """This pull request contains diagram updates rendered by the scheduled Archility job.

Review the changes under `docs/diagrams/`. The job stages no files outside that directory, and it leaves the source checkout untouched.
"""


class DeploymentError(RuntimeError):
    """Raised when a candidate cannot be safely prepared for review."""


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if (
            key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GIT_TRACE"))
            or key
            in {
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_ALLOW_PROTOCOL",
                "GIT_ASKPASS",
                "GIT_ATTR_SOURCE",
                "GIT_CEILING_DIRECTORIES",
                "GIT_COMMON_DIR",
                "GIT_CONFIG",
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_PARAMETERS",
                "GIT_DIFF_OPTS",
                "GIT_DIR",
                "GIT_EXEC_PATH",
                "GIT_EXTERNAL_DIFF",
                "GIT_GLOB_PATHSPECS",
                "GIT_GRAFT_FILE",
                "GIT_ICASE_PATHSPECS",
                "GIT_INDEX_FILE",
                "GIT_LITERAL_PATHSPECS",
                "GIT_NAMESPACE",
                "GIT_NOGLOB_PATHSPECS",
                "GIT_OBJECT_DIRECTORY",
                "GIT_PROTOCOL_FROM_USER",
                "GIT_PROXY_COMMAND",
                "GIT_QUARANTINE_PATH",
                "GIT_REPLACE_REF_BASE",
                "GIT_SHALLOW_FILE",
                "GIT_SSH",
                "GIT_SSH_COMMAND",
                "GIT_TEMPLATE_DIR",
                "GIT_WORK_TREE",
                "SSH_ASKPASS",
            }
        ):
            environment.pop(key, None)
    hardened_config = (
        ("core.hooksPath", os.devnull),
        ("core.fsmonitor", "false"),
        ("submodule.recurse", "false"),
        ("fetch.recurseSubmodules", "false"),
        ("protocol.file.allow", "never"),
        ("credential.helper", "!gh auth git-credential"),
    )
    for index, (key, value) in enumerate(hardened_config):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    environment["GIT_CONFIG_COUNT"] = str(len(hardened_config))
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_ATTR_NOSYSTEM"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_PAGER"] = "cat"
    environment["LC_ALL"] = "C"
    environment["GH_HOST"] = "github.com"
    environment["GH_PROMPT_DISABLED"] = "1"
    return environment


def _run(
    command: Sequence[str],
    *,
    environment: dict[str, str] | None = None,
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(f"could not run {command[0]}: {exc}") from exc


def _git(repository: Path, *arguments: str, check: bool = True) -> str:
    result = _run(
        ["git", "-C", str(repository), *arguments],
        environment=_git_environment(),
    )
    if check and result.returncode != 0:
        raise DeploymentError(f"git {arguments[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def _github_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GH_HOST"] = "github.com"
    environment["GH_PROMPT_DISABLED"] = "1"
    return environment


def _github_json(gh_command: str, *arguments: str) -> Any:
    result = _run([gh_command, *arguments], environment=_github_environment())
    if result.returncode != 0:
        raise DeploymentError(f"GitHub CLI request failed (exit {result.returncode})")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeploymentError("GitHub CLI returned invalid JSON") from exc


def _repository_metadata(
    gh_command: str,
    entry: visibility.RepositoryEntry,
) -> tuple[str, str]:
    payload = _github_json(
        gh_command,
        "repo",
        "view",
        entry.slug,
        "--json",
        "id,nameWithOwner,visibility,isArchived,defaultBranchRef",
    )
    if type(payload) is not dict:
        raise DeploymentError("GitHub returned invalid repository metadata")
    if payload.get("id") != entry.repository_id:
        raise DeploymentError("GitHub repository ID differs from the local registry")
    if payload.get("nameWithOwner") != entry.slug:
        raise DeploymentError("GitHub repository name differs from the local registry")
    if payload.get("visibility") not in {"PUBLIC", "PRIVATE"}:
        raise DeploymentError("GitHub repository visibility is not supported")
    if type(payload.get("isArchived")) is not bool or payload["isArchived"]:
        raise DeploymentError("GitHub repository is archived or has invalid archive state")
    branch_data = payload.get("defaultBranchRef")
    if type(branch_data) is not dict or type(branch_data.get("name")) is not str:
        raise DeploymentError("GitHub did not return a default branch")
    branch = branch_data["name"]
    ref_check = _run(["git", "check-ref-format", "--branch", branch])
    if ref_check.returncode != 0:
        raise DeploymentError("GitHub returned an invalid default branch name")
    return payload["visibility"].casefold(), branch


def _eligible_entries(
    pair: visibility.RegistryPair,
    catalog: materializer.CatalogDocument,
) -> list[tuple[materializer.CatalogEntry, str, visibility.RepositoryEntry]]:
    registry_lookup = {
        entry.repository_id: (visibility_name, entry)
        for visibility_name, entry in pair.entries
    }
    candidates = []
    for catalog_entry in catalog.repositories:
        if (
            catalog_entry.lifecycle != "active"
            or catalog_entry.sync_policy != "fetch-only"
            or catalog_entry.desired_presence != "checkout"
        ):
            continue
        registered = registry_lookup.get(catalog_entry.repository_id)
        if registered is None:
            raise DeploymentError("portfolio catalog contains an unregistered repository")
        visibility_name, repository_entry = registered
        candidates.append((catalog_entry, visibility_name, repository_entry))
    return candidates


def _open_archility_pr_exists(
    gh_command: str,
    slug: str,
    base: str,
) -> bool:
    payload = _github_json(
        gh_command,
        "pr",
        "list",
        "--repo",
        slug,
        "--state",
        "open",
        "--base",
        base,
        "--json",
        "headRefName",
        "--limit",
        "1000",
    )
    if type(payload) is not list:
        raise DeploymentError("GitHub returned an invalid pull request list")
    return any(
        type(item) is dict
        and type(item.get("headRefName")) is str
        and item["headRefName"].startswith(BRANCH_PREFIX)
        for item in payload
    )


def _remote_urls(repository: Path, entry: visibility.RepositoryEntry) -> tuple[str, str]:
    try:
        materializer.verify_checkout(repository, entry, require_clean=False)
    except materializer.MaterializerError as exc:
        raise DeploymentError("registered checkout verification failed") from exc
    fetch_url = _git(repository, "remote", "get-url", "origin")
    push_url = _git(repository, "remote", "get-url", "--push", "origin")
    for remote_url in (fetch_url, push_url):
        if visibility._normalize_github_remote(remote_url) != entry.slug:
            raise DeploymentError("origin fetch/push URL does not match the registry")
    canonical_url = f"https://github.com/{entry.slug}.git"
    return canonical_url, canonical_url


def _stage_diagram_changes(repository: Path) -> list[str]:
    if not (repository / "docs" / "diagrams").is_dir():
        return []
    _git(repository, "add", "-A", "--", "docs/diagrams")
    changed = _git(repository, "diff", "--cached", "--name-only", "-z")
    paths = [path for path in changed.split("\x00") if path]
    if any(not path.startswith("docs/diagrams/") for path in paths):
        raise DeploymentError("staged output escaped docs/diagrams")
    if any((repository / path).is_symlink() for path in paths):
        raise DeploymentError("diagram output contains a symbolic link")
    return paths


def _has_remote_branch(fetch_url: str, branch: str) -> bool:
    result = _run(
        ["git", "ls-remote", "--heads", fetch_url, f"refs/heads/{branch}"],
        environment=_git_environment(),
        timeout=120,
    )
    if result.returncode != 0:
        raise DeploymentError("could not check for an existing automation branch")
    return bool(result.stdout.strip())


def _latest_remote_sha(fetch_url: str, base: str) -> str:
    result = _run(
        ["git", "ls-remote", fetch_url, f"refs/heads/{base}"],
        environment=_git_environment(),
        timeout=120,
    )
    if result.returncode != 0:
        raise DeploymentError("could not read the registered repository default branch")
    fields = result.stdout.split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{base}":
        raise DeploymentError("GitHub default branch ref was not found on origin")
    return fields[0]


def _deploy_one(
    *,
    portfolio_root: Path,
    catalog_entry: materializer.CatalogEntry,
    visibility_name: str,
    registry_entry: visibility.RepositoryEntry,
    gh_command: str,
    archility_command: Sequence[str],
) -> str:
    display_name = (
        registry_entry.slug if visibility_name == "public" else "registered private repository"
    )
    checkout = materializer._target_path(portfolio_root, catalog_entry.relative_path)
    if not checkout.is_dir():
        print(f"skip {display_name}: no local checkout is present")
        return "skipped"
    fetch_url, push_url = _remote_urls(checkout, registry_entry)
    remote_visibility, base = _repository_metadata(gh_command, registry_entry)
    if remote_visibility != visibility_name:
        raise DeploymentError("GitHub visibility differs from the local registry")
    if _open_archility_pr_exists(gh_command, registry_entry.slug, base):
        print(f"skip {display_name}: an Archility diagram PR is already open")
        return "skipped"

    base_sha = _latest_remote_sha(fetch_url, base)
    branch = f"{BRANCH_PREFIX}{base_sha[:8]}"
    if _has_remote_branch(fetch_url, branch):
        print(f"skip {display_name}: automation branch exists without an open PR")
        return "skipped"

    with tempfile.TemporaryDirectory(prefix="archility-pr-") as temporary:
        worktree = Path(temporary) / "checkout"
        clone = _run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                "--depth=1",
                "--branch",
                base,
                "--single-branch",
                fetch_url,
                str(worktree),
            ],
            environment=_git_environment(),
            timeout=600,
        )
        if clone.returncode != 0:
            raise DeploymentError("could not clone the registered repository")
        if push_url != fetch_url:
            _git(worktree, "remote", "set-url", "--push", "origin", push_url)
        _git(worktree, "checkout", "--quiet", "--detach", f"origin/{base}")

        render = _run([*archility_command, "render", str(worktree)])
        if render.stdout.strip():
            print(render.stdout.rstrip())
        if render.stderr.strip():
            print(render.stderr.rstrip(), file=sys.stderr)
        if render.returncode != 0:
            raise DeploymentError(f"Archility render failed for {display_name}")

        paths = _stage_diagram_changes(worktree)
        if not paths:
            print(f"unchanged {display_name}: no diagram diff")
            return "unchanged"
        _git(worktree, "switch", "--create", branch)
        _git(worktree, "diff", "--cached", "--check")
        commit = _run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "user.name=Archility Diagram Bot",
                "-c",
                "user.email=archility-diagram-bot@users.noreply.github.com",
                "commit",
                "--message",
                "docs: refresh generated architecture diagrams",
            ],
            environment=_git_environment(),
        )
        if commit.returncode != 0:
            raise DeploymentError("could not commit the rendered diagrams")
        push = _run(
            ["git", "-C", str(worktree), "push", "origin", branch],
            environment=_git_environment(),
            timeout=600,
        )
        if push.returncode != 0:
            raise DeploymentError("could not push the automation branch")

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="archility-pr-", suffix=".md"
        ) as body_file:
            body_file.write(PR_BODY)
            body_file.flush()
            create = _run(
                [
                    gh_command,
                    "pr",
                    "create",
                    "--repo",
                    registry_entry.slug,
                    "--base",
                    base,
                    "--head",
                    branch,
                    "--title",
                    PR_TITLE,
                    "--body-file",
                    body_file.name,
                ],
                environment=_github_environment(),
            )
        if create.returncode != 0:
            raise DeploymentError(
                "branch pushed but GitHub could not create the pull request; "
                "review the automation branch before retrying"
            )
        print(f"created PR for {display_name}: {create.stdout.strip()}")
    return "created"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render eligible portfolio diagrams in isolated clones and create PRs."
    )
    parser.add_argument("--portfolio-root", type=Path, required=True)
    parser.add_argument("--private-registry", type=Path, required=True)
    parser.add_argument("--public-registry", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--gh-command", default="gh")
    parser.add_argument("--archility-command", nargs="+", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        portfolio_root = materializer._safe_root(arguments.portfolio_root)
        pair = visibility.load_pair(arguments.private_registry, arguments.public_registry)
        catalog = materializer.load_catalog(arguments.catalog, pair)
        candidates = _eligible_entries(pair, catalog)
        if not candidates:
            print("no active registered repositories are eligible for diagram PRs")
            return 0

        auth = _run(
            [arguments.gh_command, "auth", "status"],
            environment=_github_environment(),
        )
        if auth.returncode != 0:
            raise DeploymentError(
                "GitHub CLI authentication for github.com is required; no diagram branches were pushed"
            )

        results = {"created": 0, "unchanged": 0, "skipped": 0, "failed": 0}
        for catalog_entry, visibility_name, registry_entry in candidates:
            try:
                result = _deploy_one(
                    portfolio_root=portfolio_root,
                    catalog_entry=catalog_entry,
                    visibility_name=visibility_name,
                    registry_entry=registry_entry,
                    gh_command=arguments.gh_command,
                    archility_command=arguments.archility_command,
                )
            except (DeploymentError, materializer.MaterializerError) as exc:
                results["failed"] += 1
                display_name = (
                    registry_entry.slug
                    if visibility_name == "public"
                    else "registered private repository"
                )
                print(f"error {display_name}: {exc}", file=sys.stderr)
            else:
                results[result] += 1

        print(
            "diagram PR summary: "
            + ", ".join(f"{key}={value}" for key, value in results.items())
        )
        return 1 if results["failed"] else 0
    except (DeploymentError, materializer.MaterializerError, visibility.RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
