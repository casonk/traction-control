#!/usr/bin/env python3
"""Materialize and verify the repositories in traction-control's portfolio."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence

import repository_visibility as visibility


SCHEMA_VERSION = 1
ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "registry_generation",
    "catalog_generation",
    "repositories",
}
ENTRY_KEYS = {
    "repository_id",
    "relative_path",
    "lifecycle",
    "sync_policy",
    "desired_presence",
}
LIFECYCLES = {"active", "archived"}
SYNC_POLICIES = {"fetch-only", "manual"}
DESIRED_PRESENCE = {"checkout", "absent"}


class MaterializerError(Exception):
    """Raised when catalog or checkout state cannot be trusted."""


@dataclass(frozen=True)
class CatalogEntry:
    repository_id: str
    relative_path: str
    lifecycle: str
    sync_policy: str
    desired_presence: str


@dataclass(frozen=True)
class CatalogDocument:
    path: Path
    registry_id: str
    registry_generation: int
    catalog_generation: int
    repositories: tuple[CatalogEntry, ...]


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    label: str,
) -> None:
    actual = set(value)
    if actual == expected:
        return
    details: list[str] = []
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise MaterializerError(f"{label} has invalid keys ({'; '.join(details)})")


def _validate_relative_path(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise MaterializerError("catalog relative_path must be a non-empty trimmed string")
    if unicodedata.normalize("NFC", value) != value:
        raise MaterializerError(f"catalog path must use NFC Unicode: {value!r}")
    if any(
        unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise MaterializerError(f"catalog path contains a control character: {value!r}")
    if "\\" in value:
        raise MaterializerError(f"catalog path must use POSIX separators: {value!r}")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise MaterializerError(f"catalog path contains an unsafe component: {value!r}")
    if any(part.casefold() == ".git" for part in raw_parts):
        raise MaterializerError(f"catalog path contains an unsafe component: {value!r}")
    pure_path = PurePosixPath(value)
    if pure_path.is_absolute() or str(pure_path) != value:
        raise MaterializerError(f"catalog path must be normalized and relative: {value!r}")
    return value


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    try:
        return visibility._object_without_duplicate_keys(pairs)
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc


def load_catalog(
    catalog_path: str | os.PathLike[str],
    pair: visibility.RegistryPair,
    *,
    allow_stale_registry: bool = False,
) -> CatalogDocument:
    path = Path(catalog_path)
    try:
        raw = visibility._read_secure_regular_file(path)
        visibility._require_ignored_or_outside_git(path)
        payload = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc
    except UnicodeError as exc:
        raise MaterializerError(f"catalog is not valid UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MaterializerError(
            f"invalid JSON in catalog {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc

    if type(payload) is not dict:
        raise MaterializerError(f"catalog root must be a JSON object: {path}")
    _require_exact_keys(payload, ROOT_KEYS, f"catalog {path}")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise MaterializerError(f"catalog {path} must use schema_version 1")
    if payload["registry_id"] != pair.registry_id:
        raise MaterializerError("catalog registry_id does not match the visibility registry")
    registry_generation = payload["registry_generation"]
    if type(registry_generation) is not int or registry_generation < 0:
        raise MaterializerError(
            "catalog registry_generation must be a non-negative integer"
        )
    if allow_stale_registry:
        if registry_generation > pair.generation:
            raise MaterializerError(
                "catalog registry_generation is newer than the visibility registry"
            )
    elif registry_generation != pair.generation:
        raise MaterializerError(
            "catalog registry_generation does not match the visibility registry"
        )
    catalog_generation = payload["catalog_generation"]
    if type(catalog_generation) is not int or catalog_generation < 0:
        raise MaterializerError("catalog_generation must be a non-negative integer")
    raw_repositories = payload["repositories"]
    if type(raw_repositories) is not list:
        raise MaterializerError("catalog repositories must be a JSON array")

    entries: list[CatalogEntry] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, raw_entry in enumerate(raw_repositories):
        if type(raw_entry) is not dict:
            raise MaterializerError(f"catalog entry {index} must be a JSON object")
        _require_exact_keys(raw_entry, ENTRY_KEYS, f"catalog entry {index}")
        try:
            repository_id = visibility._validate_repository_id(
                raw_entry["repository_id"]
            )
        except visibility.RegistryError as exc:
            raise MaterializerError(str(exc)) from exc
        relative_path = _validate_relative_path(raw_entry["relative_path"])
        lifecycle = raw_entry["lifecycle"]
        sync_policy = raw_entry["sync_policy"]
        desired_presence = raw_entry["desired_presence"]
        if lifecycle not in LIFECYCLES:
            raise MaterializerError(
                f"catalog entry {index} has unsupported lifecycle: {lifecycle!r}"
            )
        if sync_policy not in SYNC_POLICIES:
            raise MaterializerError(
                f"catalog entry {index} has unsupported sync_policy: {sync_policy!r}"
            )
        if desired_presence not in DESIRED_PRESENCE:
            raise MaterializerError(
                f"catalog entry {index} has unsupported desired_presence: "
                f"{desired_presence!r}"
            )
        path_key = relative_path.casefold()
        if repository_id in seen_ids:
            raise MaterializerError(
                f"duplicate repository_id in catalog: {repository_id}"
            )
        if path_key in seen_paths:
            raise MaterializerError(
                f"duplicate case-insensitive relative_path in catalog: {relative_path}"
            )
        seen_ids.add(repository_id)
        seen_paths.add(path_key)
        entries.append(
            CatalogEntry(
                repository_id=repository_id,
                relative_path=relative_path,
                lifecycle=lifecycle,
                sync_policy=sync_policy,
                desired_presence=desired_presence,
            )
        )

    expected_ids = {entry.repository_id for _, entry in pair.entries}
    missing_ids = sorted(expected_ids - seen_ids)
    unknown_ids = sorted(seen_ids - expected_ids)
    stale_growth = allow_stale_registry and registry_generation < pair.generation
    if unknown_ids:
        raise MaterializerError(
            "catalog contains registered repository IDs that were removed from "
            "the visibility registry"
        )
    if missing_ids and not stale_growth:
        details: list[str] = []
        if missing_ids:
            details.append(f"missing {len(missing_ids)} registered repository IDs")
        raise MaterializerError(f"catalog coverage mismatch ({'; '.join(details)})")

    ordered = sorted(
        entries,
        key=lambda entry: (entry.relative_path.casefold(), entry.repository_id),
    )
    if entries != ordered:
        raise MaterializerError("catalog repositories are not deterministically sorted")

    pure_paths = [
        (entry.relative_path, PurePosixPath(entry.relative_path).parts)
        for entry in entries
    ]
    for index, (path_value, parts) in enumerate(pure_paths):
        for other_path, other_parts in pure_paths[index + 1 :]:
            shorter = min(len(parts), len(other_parts))
            if (
                tuple(part.casefold() for part in parts[:shorter])
                == tuple(part.casefold() for part in other_parts[:shorter])
                and len(parts) != len(other_parts)
            ):
                raise MaterializerError(
                    "catalog checkout paths may not contain one another: "
                    f"{path_value!r}, {other_path!r}"
                )

    return CatalogDocument(
        path=path,
        registry_id=pair.registry_id,
        registry_generation=registry_generation,
        catalog_generation=catalog_generation,
        repositories=tuple(entries),
    )


def _entry_lookup(
    pair: visibility.RegistryPair,
) -> dict[str, tuple[str, visibility.RepositoryEntry]]:
    return {
        entry.repository_id: (entry_visibility, entry)
        for entry_visibility, entry in pair.entries
    }


def _load_pair(private_path: str, public_path: str) -> visibility.RegistryPair:
    try:
        return visibility.load_pair(private_path, public_path)
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc


def _registry_pair_state(
    pair: visibility.RegistryPair,
) -> tuple[visibility.RegistryDocument, visibility.RegistryDocument]:
    """Return every parsed field from both halves of a registry snapshot."""

    return pair.private, pair.public


def _reload_registry_snapshot(
    snapshot: visibility.RegistryPair,
) -> visibility.RegistryPair:
    try:
        current = visibility.load_pair(
            snapshot.private.path,
            snapshot.public.path,
        )
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc
    if _registry_pair_state(current) != _registry_pair_state(snapshot):
        raise MaterializerError(
            "visibility registry content or generation changed before "
            "the locked operation"
        )
    return current


@contextmanager
def _locked_registry_snapshot(
    snapshot: visibility.RegistryPair,
) -> Iterator[visibility.RegistryPair]:
    """Lock the registry and fail if the caller's complete snapshot is stale.

    Materializer operations always acquire this registry lock before the
    catalog lock. Visibility-registry writers never acquire the catalog lock,
    so this ordering prevents a lock cycle while keeping the authoritative
    membership, slugs, and visibility immutable for the yielded operation.
    """

    try:
        with visibility._RegistryLock(snapshot.private.path):
            current = _reload_registry_snapshot(snapshot)
            yield current
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc


def _run(
    command: Sequence[str],
    *,
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    environment: dict[str, str] | None = None
    if command and command[0] == "git":
        environment = os.environ.copy()
        raw_count = environment.get("GIT_CONFIG_COUNT", "0")
        try:
            config_count = int(raw_count, 10)
        except ValueError as exc:
            raise MaterializerError("GIT_CONFIG_COUNT must be an integer") from exc
        if config_count < 0 or config_count > 10_000:
            raise MaterializerError("GIT_CONFIG_COUNT is outside the safe range")
        hardened_config = (
            ("core.hooksPath", os.devnull),
            ("core.fsmonitor", "false"),
            ("submodule.recurse", "false"),
            ("fetch.recurseSubmodules", "false"),
        )
        for offset, (key, value) in enumerate(hardened_config):
            index = config_count + offset
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
        environment["GIT_CONFIG_COUNT"] = str(
            config_count + len(hardened_config)
        )
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_SYSTEM"] = os.devnull
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment.pop("GIT_TEMPLATE_DIR", None)
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaterializerError(f"cannot run command {command[0]!r}: {exc}") from exc


def _safe_root(root_value: str | os.PathLike[str]) -> Path:
    root = Path(root_value)
    if not root.is_dir():
        raise MaterializerError(f"portfolio root is not a directory: {root}")
    if root.is_symlink():
        raise MaterializerError(f"portfolio root must not be a symlink: {root}")
    root = root.resolve()
    if root == Path(root.anchor):
        raise MaterializerError("portfolio root cannot be the filesystem root")
    return root


def _target_path(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    target = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise MaterializerError(f"catalog path traverses a symlink: {current}")
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MaterializerError(f"catalog path escapes portfolio root: {target}") from exc
    return target


def _configured_remote_urls(
    repository: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    remotes_result = _run(["git", "-C", str(repository), "remote"])
    remotes = tuple(
        remote.strip()
        for remote in remotes_result.stdout.splitlines()
        if remote.strip()
    )
    if remotes_result.returncode != 0:
        raise MaterializerError(f"cannot inspect checkout remotes: {repository}")
    urls: list[str] = []
    for remote in remotes:
        for mode_arguments, mode_label in (
            (("--all",), "fetch"),
            (("--push", "--all"), "push"),
        ):
            result = _run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "get-url",
                    *mode_arguments,
                    remote,
                ]
            )
            if result.returncode != 0:
                raise MaterializerError(
                    f"cannot resolve {mode_label} URLs for remote {remote}: "
                    f"{repository}"
                )
            urls.extend(url.strip() for url in result.stdout.splitlines() if url.strip())
    return remotes, tuple(dict.fromkeys(urls))


def _remote_urls(repository: Path) -> tuple[str, ...]:
    remotes, urls = _configured_remote_urls(repository)
    if not remotes:
        raise MaterializerError(f"checkout has no configured remotes: {repository}")
    if "origin" not in remotes:
        raise MaterializerError(f"checkout has no origin remote: {repository}")
    if not urls:
        raise MaterializerError(f"checkout has no usable remote URLs: {repository}")
    return urls


def _read_git_config(
    repository: Path,
    arguments: Sequence[str],
    *,
    label: str,
) -> subprocess.CompletedProcess[str]:
    result = _run(["git", "-C", str(repository), "config", *arguments])
    if result.returncode not in {0, 1}:
        raise MaterializerError(f"cannot inspect {label}: {repository}")
    return result


def _verify_full_checkout(repository: Path) -> None:
    promisor_pack_directory = repository / ".git" / "objects" / "pack"
    if promisor_pack_directory.is_dir() and any(
        promisor_pack_directory.glob("*.promisor")
    ):
        raise MaterializerError(f"partial clone is not allowed: {repository}")

    shallow = _run(
        ["git", "-C", str(repository), "rev-parse", "--is-shallow-repository"]
    )
    if shallow.returncode != 0:
        raise MaterializerError(f"cannot inspect shallow clone state: {repository}")
    if shallow.stdout.strip().casefold() != "false":
        raise MaterializerError(f"shallow checkout is not allowed: {repository}")

    sparse = _read_git_config(
        repository,
        ("--bool", "core.sparseCheckout"),
        label="sparse checkout state",
    )
    if sparse.returncode == 0:
        sparse_value = sparse.stdout.strip().casefold()
        if sparse_value not in {"true", "false"}:
            raise MaterializerError(
                f"sparse checkout state is malformed: {repository}"
            )
        if sparse_value == "true":
            raise MaterializerError(f"sparse checkout is not allowed: {repository}")

    partial_extension = _read_git_config(
        repository,
        ("--get", "extensions.partialClone"),
        label="partial clone state",
    )
    if partial_extension.returncode == 0 and partial_extension.stdout.strip():
        raise MaterializerError(f"partial clone is not allowed: {repository}")

    promisor_remotes = _read_git_config(
        repository,
        ("--get-regexp", r"^remote\..*\.promisor$"),
        label="promisor remote state",
    )
    if promisor_remotes.returncode == 0:
        for line in promisor_remotes.stdout.splitlines():
            fields = line.rsplit(maxsplit=1)
            if len(fields) != 2 or fields[1].casefold() not in {"true", "false"}:
                raise MaterializerError(
                    f"promisor remote state is malformed: {repository}"
                )
            if fields[1].casefold() == "true":
                raise MaterializerError(f"partial clone is not allowed: {repository}")

    partial_filters = _read_git_config(
        repository,
        ("--get-regexp", r"^remote\..*\.partialclonefilter$"),
        label="partial clone filter state",
    )
    if partial_filters.returncode == 0 and partial_filters.stdout.strip():
        raise MaterializerError(f"partial clone is not allowed: {repository}")


def verify_checkout(
    repository: Path,
    expected_entry: visibility.RepositoryEntry,
    *,
    require_clean: bool,
) -> None:
    if repository.is_symlink():
        raise MaterializerError(f"checkout path must not be a symlink: {repository}")
    if not repository.is_dir():
        raise MaterializerError(f"checkout path is not a directory: {repository}")
    git_marker = repository / ".git"
    if not git_marker.is_dir() or git_marker.is_symlink():
        raise MaterializerError(
            f"checkout must be a standalone Git repository, not a linked worktree: "
            f"{repository}"
        )
    top_level = _run(
        ["git", "-C", str(repository), "rev-parse", "--show-toplevel"]
    )
    if top_level.returncode != 0 or not top_level.stdout.strip():
        raise MaterializerError(f"checkout is not a Git worktree: {repository}")
    if Path(top_level.stdout.strip()).resolve() != repository.resolve():
        raise MaterializerError(
            f"checkout target is nested inside another repository: {repository}"
        )
    expected_slug = expected_entry.slug.casefold()
    for remote_url in _remote_urls(repository):
        observed_slug = visibility._normalize_github_remote(remote_url)
        if observed_slug is None:
            raise MaterializerError(
                f"checkout remote is not a canonical GitHub repository: {repository}"
            )
        if observed_slug.casefold() != expected_slug:
            raise MaterializerError(
                f"checkout remote identity mismatch at {repository}: "
                f"expected {expected_entry.slug}, found {observed_slug}"
            )
    _verify_full_checkout(repository)
    if require_clean:
        status = _run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ]
        )
        if status.returncode != 0:
            raise MaterializerError(f"cannot inspect checkout status: {repository}")
        if status.stdout:
            raise MaterializerError(
                f"refusing to fetch a dirty/staged/untracked checkout: {repository}"
            )


def _verify_remote_registry(
    pair: visibility.RegistryPair,
    gh_command: str,
) -> None:
    try:
        visibility.audit_pair(
            pair,
            gh_command=gh_command,
            portfolio_roots=[],
            skip_github=False,
        )
    except (visibility.RegistryError, visibility.AuditFailure) as exc:
        if isinstance(exc, visibility.AuditFailure):
            detail = "; ".join(exc.failures)
        else:
            detail = str(exc)
        raise MaterializerError(f"GitHub registry audit failed: {detail}") from exc


def _read_remote_entry_archive_state(
    expected_visibility: str,
    entry: visibility.RepositoryEntry,
    gh_command: str,
) -> bool:
    try:
        gh_path = visibility._resolve_gh_command(gh_command)
        result = visibility._run(
            [
                gh_path,
                "repo",
                "view",
                entry.slug,
                "--json",
                "id,nameWithOwner,visibility,isArchived",
            ],
            environment=visibility._github_environment(),
        )
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc
    if result.returncode != 0:
        raise MaterializerError(f"cannot verify GitHub repository: {entry.slug}")
    try:
        payload = json.loads(
            result.stdout,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (json.JSONDecodeError, MaterializerError) as exc:
        raise MaterializerError(
            f"GitHub returned malformed repository metadata: {entry.slug}"
        ) from exc
    if type(payload) is not dict:
        raise MaterializerError(
            f"GitHub returned non-object repository metadata: {entry.slug}"
        )
    if (
        payload.get("id") != entry.repository_id
        or payload.get("nameWithOwner") != entry.slug
        or payload.get("visibility") != expected_visibility.upper()
    ):
        raise MaterializerError(f"GitHub identity or visibility drift: {entry.slug}")
    if type(payload.get("isArchived")) is not bool:
        raise MaterializerError(f"GitHub archive state is malformed: {entry.slug}")
    return payload["isArchived"]


def _verify_remote_entry(
    expected_visibility: str,
    entry: visibility.RepositoryEntry,
    lifecycle: str,
    gh_command: str,
) -> None:
    observed_archived = _read_remote_entry_archive_state(
        expected_visibility,
        entry,
        gh_command,
    )
    if observed_archived is not (lifecycle == "archived"):
        raise MaterializerError(f"GitHub archive-state drift: {entry.slug}")


def _ensure_private_directory(directory: Path) -> None:
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path_metadata = directory.lstat()
    except OSError as exc:
        raise MaterializerError(
            f"cannot prepare catalog directory {directory}: {exc}"
        ) from exc
    if stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISDIR(
        path_metadata.st_mode
    ):
        raise MaterializerError(
            f"catalog directory must be a real directory: {directory}"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        raise MaterializerError(
            f"catalog directory must be a real directory: {directory}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_dev != path_metadata.st_dev
            or metadata.st_ino != path_metadata.st_ino
        ):
            raise MaterializerError(
                f"catalog directory must be a real directory: {directory}"
            )
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise MaterializerError(
                f"catalog directory is not owned by the current user: {directory}"
            )
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            os.fchmod(descriptor, 0o700)
            metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise MaterializerError(
                f"catalog directory must have owner-only mode 0700: {directory}"
            )
    except OSError as exc:
        raise MaterializerError(
            f"cannot secure catalog directory {directory}: {exc}"
        ) from exc
    finally:
        os.close(descriptor)


class _CatalogLock:
    def __init__(self, catalog_path: Path) -> None:
        self.path = catalog_path.parent / ".portfolio-materializer.lock"
        self.descriptor: int | None = None

    def __enter__(self) -> "_CatalogLock":
        _ensure_private_directory(self.path.parent)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.descriptor = os.open(self.path, flags, 0o600)
            metadata = os.fstat(self.descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise MaterializerError(
                    f"catalog lock is not a regular file: {self.path}"
                )
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise MaterializerError(
                    f"catalog lock is not owned by the current user: {self.path}"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise MaterializerError(
                    f"catalog lock has unsafe permissions: {self.path}"
                )
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        except BaseException:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


def _catalog_payload(
    pair: visibility.RegistryPair,
    mappings: dict[str, str],
) -> dict[str, Any]:
    rows = [
        {
            "repository_id": repository_id,
            "relative_path": relative_path,
            "lifecycle": "active",
            "sync_policy": "fetch-only",
            "desired_presence": "checkout",
        }
        for repository_id, relative_path in mappings.items()
    ]
    rows.sort(
        key=lambda row: (
            str(row["relative_path"]).casefold(),
            str(row["repository_id"]),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_id": pair.registry_id,
        "registry_generation": pair.generation,
        "catalog_generation": 0,
        "repositories": rows,
    }


def _catalog_state(catalog: CatalogDocument) -> tuple[object, ...]:
    return (
        catalog.registry_id,
        catalog.registry_generation,
        catalog.catalog_generation,
        catalog.repositories,
    )


def _reload_catalog_snapshot(
    pair: visibility.RegistryPair,
    snapshot: CatalogDocument,
) -> CatalogDocument:
    current_pair = _reload_registry_snapshot(pair)
    current = load_catalog(snapshot.path, current_pair)
    if _catalog_state(current) != _catalog_state(snapshot):
        raise MaterializerError(
            "catalog content or generation changed before the locked operation"
        )
    return current


def _validate_mapping_paths(mappings: dict[str, str]) -> None:
    normalized: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for relative_path in mappings.values():
        relative_path = _validate_relative_path(relative_path)
        key = relative_path.casefold()
        if key in seen:
            raise MaterializerError(f"catalog path collision: {relative_path}")
        seen.add(key)
        normalized.append((relative_path, PurePosixPath(relative_path).parts))
    normalized.sort(key=lambda value: value[0].casefold())
    for index, (path_value, parts) in enumerate(normalized):
        for other_path, other_parts in normalized[index + 1 :]:
            shorter = min(len(parts), len(other_parts))
            if (
                tuple(part.casefold() for part in parts[:shorter])
                == tuple(part.casefold() for part in other_parts[:shorter])
                and len(parts) != len(other_parts)
            ):
                raise MaterializerError(
                    "catalog checkout paths may not contain one another: "
                    f"{path_value!r}, {other_path!r}"
                )


def _write_secure_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    try:
        visibility._require_ignored_or_outside_git(path)
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise MaterializerError(f"refusing to overwrite existing catalog: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _replace_secure_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    try:
        visibility._read_secure_regular_file(path)
        visibility._require_ignored_or_outside_git(path)
    except visibility.RegistryError as exc:
        raise MaterializerError(str(exc)) from exc
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp.local.json",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def refresh_archive_states(
    pair: visibility.RegistryPair,
    catalog: CatalogDocument,
    *,
    gh_command: str,
) -> tuple[CatalogDocument, int]:
    with _locked_registry_snapshot(pair) as current_pair:
        lookup = _entry_lookup(current_pair)
        observed: dict[str, str] = {}
        for catalog_entry in catalog.repositories:
            expected_visibility, registry_entry = lookup[
                catalog_entry.repository_id
            ]
            archived = _read_remote_entry_archive_state(
                expected_visibility,
                registry_entry,
                gh_command,
            )
            observed[catalog_entry.repository_id] = (
                "archived" if archived else "active"
            )
        changed = sum(
            observed[entry.repository_id] != entry.lifecycle
            for entry in catalog.repositories
        )
        if changed == 0:
            with _CatalogLock(catalog.path):
                current = _reload_catalog_snapshot(current_pair, catalog)
            return current, 0
        rows = [
            {
                "repository_id": entry.repository_id,
                "relative_path": entry.relative_path,
                "lifecycle": observed[entry.repository_id],
                "sync_policy": entry.sync_policy,
                "desired_presence": entry.desired_presence,
            }
            for entry in catalog.repositories
        ]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "registry_id": catalog.registry_id,
            "registry_generation": catalog.registry_generation,
            "catalog_generation": catalog.catalog_generation + 1,
            "repositories": rows,
        }
        with _CatalogLock(catalog.path):
            _reload_catalog_snapshot(current_pair, catalog)
            _replace_secure_json(catalog.path, payload)
            refreshed = load_catalog(catalog.path, current_pair)
            _reload_registry_snapshot(current_pair)
        return refreshed, changed


def initialize_catalog(
    pair: visibility.RegistryPair,
    catalog_path: Path,
    root: Path,
) -> CatalogDocument:
    root = root.resolve()
    mappings = _discover_registered_mappings(pair, root)
    used_paths = {path.casefold() for path in mappings.values()}
    for _, entry in pair.entries:
        if entry.repository_id in mappings:
            continue
        owner, repository_name = entry.slug.split("/", 1)
        candidate = f"github/{owner}/{repository_name}"
        if candidate.casefold() in used_paths:
            raise MaterializerError(f"default catalog path collides: {candidate}")
        mappings[entry.repository_id] = candidate
        used_paths.add(candidate.casefold())

    _validate_mapping_paths(mappings)
    with _locked_registry_snapshot(pair) as current_pair:
        with _CatalogLock(catalog_path):
            _reload_registry_snapshot(current_pair)
            _write_secure_json(
                catalog_path,
                _catalog_payload(current_pair, mappings),
            )
            initialized = load_catalog(catalog_path, current_pair)
            _reload_registry_snapshot(current_pair)
        return initialized


def _discover_registered_mappings(
    pair: visibility.RegistryPair,
    root: Path,
) -> dict[str, str]:
    lookup_by_slug = {
        entry.slug.casefold(): entry
        for _, entry in pair.entries
    }
    mappings: dict[str, str] = {}
    for repository in visibility._discover_git_repositories(root):
        remotes, urls = _configured_remote_urls(repository)
        if not remotes:
            continue
        registered_identities: dict[str, visibility.RepositoryEntry] = {}
        for remote_url in urls:
            slug = visibility._normalize_github_remote(remote_url)
            if slug is None:
                continue
            registry_entry = lookup_by_slug.get(slug.casefold())
            if registry_entry is not None:
                registered_identities[registry_entry.repository_id] = (
                    registry_entry
                )
        if not registered_identities:
            continue
        if len(registered_identities) != 1:
            raise MaterializerError(
                f"cannot initialize from mixed remote identities: {repository}"
            )
        registry_entry = next(iter(registered_identities.values()))
        verify_checkout(repository, registry_entry, require_clean=False)
        repository_id = registry_entry.repository_id
        relative_path = repository.resolve().relative_to(root).as_posix()
        _validate_relative_path(relative_path)
        if repository_id in mappings:
            raise MaterializerError(
                f"registered identity has multiple local checkouts: {repository_id}"
            )
        mappings[repository_id] = relative_path
    return mappings


def reconcile_catalog(
    pair: visibility.RegistryPair,
    catalog_path: Path,
    root: Path,
) -> tuple[CatalogDocument, int]:
    with _locked_registry_snapshot(pair) as current_pair:
        with _CatalogLock(catalog_path):
            _reload_registry_snapshot(current_pair)
            stale = load_catalog(
                catalog_path,
                current_pair,
                allow_stale_registry=True,
            )
            if stale.registry_generation == current_pair.generation:
                return load_catalog(catalog_path, current_pair), 0

            current_ids = {
                entry.repository_id for _, entry in current_pair.entries
            }
            existing_ids = {
                entry.repository_id for entry in stale.repositories
            }
            removed_ids = existing_ids - current_ids
            if removed_ids:
                raise MaterializerError(
                    "catalog contains registered repository IDs that were removed "
                    "from the visibility registry"
                )
            new_ids = current_ids - existing_ids
            discovered = (
                _discover_registered_mappings(current_pair, root.resolve())
                if new_ids
                else {}
            )
            lookup = _entry_lookup(current_pair)
            mappings = {
                entry.repository_id: entry.relative_path
                for entry in stale.repositories
            }
            for repository_id in sorted(new_ids):
                if repository_id in discovered:
                    relative_path = discovered[repository_id]
                else:
                    _, registry_entry = lookup[repository_id]
                    owner, repository_name = registry_entry.slug.split("/", 1)
                    relative_path = f"github/{owner}/{repository_name}"
                mappings[repository_id] = relative_path
            _validate_mapping_paths(mappings)

            rows = [
                {
                    "repository_id": entry.repository_id,
                    "relative_path": entry.relative_path,
                    "lifecycle": entry.lifecycle,
                    "sync_policy": entry.sync_policy,
                    "desired_presence": entry.desired_presence,
                }
                for entry in stale.repositories
            ]
            rows.extend(
                {
                    "repository_id": repository_id,
                    "relative_path": mappings[repository_id],
                    "lifecycle": "active",
                    "sync_policy": "fetch-only",
                    "desired_presence": "checkout",
                }
                for repository_id in new_ids
            )
            rows.sort(
                key=lambda row: (
                    str(row["relative_path"]).casefold(),
                    str(row["repository_id"]),
                )
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "registry_id": current_pair.registry_id,
                "registry_generation": current_pair.generation,
                "catalog_generation": stale.catalog_generation + 1,
                "repositories": rows,
            }
            _replace_secure_json(catalog_path, payload)
            reconciled = load_catalog(catalog_path, current_pair)
            _reload_registry_snapshot(current_pair)
        return reconciled, len(new_ids)


def plan_operations(
    pair: visibility.RegistryPair,
    catalog: CatalogDocument,
    root: Path,
    *,
    show_slugs: bool = False,
) -> tuple[str, ...]:
    lookup = _entry_lookup(pair)
    operations: list[str] = []
    catalog_paths: set[Path] = set()
    for catalog_entry in catalog.repositories:
        entry_visibility, registry_entry = lookup[catalog_entry.repository_id]
        if show_slugs or entry_visibility == "public":
            display_location = catalog_entry.relative_path
        else:
            display_location = f"repository-id:{catalog_entry.repository_id}"
        try:
            target = _target_path(root, catalog_entry.relative_path)
            catalog_paths.add(target.resolve(strict=False))
            if catalog_entry.desired_presence == "absent":
                action = "unexpected-present" if target.exists() else "absent"
            elif not target.exists():
                action = "clone"
            else:
                verify_checkout(target, registry_entry, require_clean=False)
                action = (
                    "fetch"
                    if catalog_entry.sync_policy == "fetch-only"
                    else "manual"
                )
        except MaterializerError as exc:
            if show_slugs:
                raise
            raise MaterializerError(
                f"checkout verification failed for {display_location}"
            ) from exc
        fields = [action, display_location]
        if show_slugs:
            fields.append(registry_entry.slug)
        operations.append("\t".join(fields))
    for repository in visibility._discover_git_repositories(root.resolve()):
        if repository.resolve() not in catalog_paths:
            relative_path = repository.resolve().relative_to(root.resolve()).as_posix()
            if show_slugs:
                display_location = relative_path
            else:
                digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
                display_location = f"path-sha256:{digest[:16]}"
            operations.append(
                f"review-unmanaged\t{display_location}"
            )
    return tuple(operations)


def audit_catalog(
    pair: visibility.RegistryPair,
    catalog: CatalogDocument,
    root: Path,
) -> None:
    lookup = _entry_lookup(pair)
    failures: list[str] = []
    catalog_paths: set[Path] = set()
    for catalog_entry in catalog.repositories:
        _, registry_entry = lookup[catalog_entry.repository_id]
        try:
            target = _target_path(root, catalog_entry.relative_path)
            catalog_paths.add(target.resolve(strict=False))
            if catalog_entry.desired_presence == "absent":
                if target.exists() or target.is_symlink():
                    raise MaterializerError(
                        f"checkout should be absent according to catalog: {target}"
                    )
                continue
            verify_checkout(target, registry_entry, require_clean=False)
        except MaterializerError as exc:
            failures.append(str(exc))
    for repository in visibility._discover_git_repositories(root.resolve()):
        if repository.resolve() not in catalog_paths:
            failures.append(
                f"unmanaged local checkout is absent from the GitHub catalog: "
                f"{repository}"
            )
    if failures:
        raise MaterializerError("portfolio catalog audit failed: " + "; ".join(failures))


def materialize(
    pair: visibility.RegistryPair,
    catalog: CatalogDocument,
    root: Path,
    *,
    clone_protocol: str,
    gh_command: str,
    skip_github: bool,
) -> None:
    with _locked_registry_snapshot(pair) as current_pair:
        lookup = _entry_lookup(current_pair)
        with _CatalogLock(catalog.path):
            current_catalog = _reload_catalog_snapshot(current_pair, catalog)
            for catalog_entry in current_catalog.repositories:
                expected_visibility, registry_entry = lookup[
                    catalog_entry.repository_id
                ]
                target = _target_path(root, catalog_entry.relative_path)
                if catalog_entry.desired_presence == "absent":
                    if target.exists() or target.is_symlink():
                        raise MaterializerError(
                            f"refusing desired-absent path that is present: {target}"
                        )
                    continue
                if target.exists() or target.is_symlink():
                    verify_checkout(target, registry_entry, require_clean=False)
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _target_path(root, catalog_entry.relative_path)
                temporary_path = Path(
                    tempfile.mkdtemp(
                        prefix=f".{target.name}.materialize.",
                        dir=target.parent,
                    )
                )
                try:
                    if clone_protocol == "ssh":
                        clone_url = f"git@github.com:{registry_entry.slug}.git"
                    else:
                        clone_url = f"https://github.com/{registry_entry.slug}.git"
                    result = _run(
                        [
                            "git",
                            "clone",
                            "--no-recurse-submodules",
                            "--origin",
                            "origin",
                            clone_url,
                            str(temporary_path),
                        ],
                        timeout=1800.0,
                    )
                    if result.returncode != 0:
                        raise MaterializerError(
                            f"Git clone failed for {registry_entry.slug}: "
                            f"{result.stderr.strip()}"
                        )
                    verify_checkout(
                        temporary_path,
                        registry_entry,
                        require_clean=False,
                    )
                    if not skip_github:
                        _verify_remote_entry(
                            expected_visibility,
                            registry_entry,
                            catalog_entry.lifecycle,
                            gh_command,
                        )
                    if target.exists() or target.is_symlink():
                        raise MaterializerError(
                            f"checkout target appeared during clone: {target}"
                        )
                    os.rename(temporary_path, target)
                finally:
                    if temporary_path.exists():
                        shutil.rmtree(temporary_path)
            _reload_registry_snapshot(current_pair)


def synchronize(
    pair: visibility.RegistryPair,
    catalog: CatalogDocument,
    root: Path,
) -> None:
    with _locked_registry_snapshot(pair) as current_pair:
        lookup = _entry_lookup(current_pair)
        with _CatalogLock(catalog.path):
            current_catalog = _reload_catalog_snapshot(current_pair, catalog)
            for catalog_entry in current_catalog.repositories:
                if catalog_entry.desired_presence != "checkout":
                    continue
                if catalog_entry.sync_policy == "manual":
                    continue
                _, registry_entry = lookup[catalog_entry.repository_id]
                target = _target_path(root, catalog_entry.relative_path)
                if not target.exists():
                    raise MaterializerError(
                        f"missing checkout; run materialize first: {target}"
                    )
                verify_checkout(target, registry_entry, require_clean=True)
                result = _run(
                    [
                        "git",
                        "-C",
                        str(target),
                        "fetch",
                        "--no-recurse-submodules",
                        "origin",
                    ]
                )
                if result.returncode != 0:
                    raise MaterializerError(
                        f"Git fetch failed for {registry_entry.slug}: "
                        f"{result.stderr.strip()}"
                    )
                verify_checkout(target, registry_entry, require_clean=True)
            _reload_registry_snapshot(current_pair)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--private", required=True)
    parser.add_argument("--public", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--portfolio-root", required=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the fail-closed master portfolio checkout catalog."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="Create a secure catalog from existing checkouts and deterministic defaults.",
    )
    _add_common_arguments(init_parser)

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help=(
            "Reconcile a stale catalog after visibility-registry generation "
            "growth."
        ),
    )
    _add_common_arguments(reconcile_parser)

    validate_parser = subparsers.add_parser("validate")
    _add_common_arguments(validate_parser)

    for command in ("plan", "materialize", "sync", "audit", "refresh"):
        command_parser = subparsers.add_parser(command)
        _add_common_arguments(command_parser)
        command_parser.add_argument("--gh", default="gh")
        if command != "refresh":
            command_parser.add_argument(
                "--skip-github",
                action="store_true",
                help=(
                    "Skip hosted identity checks; suitable only for controlled "
                    "offline tests."
                ),
            )
        if command == "materialize":
            command_parser.add_argument(
                "--clone-protocol",
                choices=("https", "ssh"),
                default="ssh",
                help=(
                    "Clone transport (default: ssh; HTTPS requires explicit "
                    "non-global credentials because Git config is isolated)."
                ),
            )
        if command == "plan":
            command_parser.add_argument(
                "--show-slugs",
                action="store_true",
                help=(
                    "Include full checkout paths and GitHub owner/repository "
                    "slugs in plan output."
                ),
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        pair = _load_pair(args.private, args.public)
        root = _safe_root(args.portfolio_root)
        if args.command == "init":
            catalog = initialize_catalog(pair, Path(args.catalog), root)
            print(
                f"initialized catalog generation {catalog.catalog_generation}: "
                f"{len(catalog.repositories)} repositories"
            )
            return 0
        if args.command == "reconcile":
            catalog, added = reconcile_catalog(
                pair,
                Path(args.catalog),
                root,
            )
            print(
                f"reconciled catalog generation {catalog.catalog_generation}: "
                f"{added} registered repository addition(s)"
            )
            return 0
        catalog = load_catalog(args.catalog, pair)
        if args.command == "validate":
            print(
                f"valid catalog generation {catalog.catalog_generation}: "
                f"{len(catalog.repositories)} repositories"
            )
            return 0
        if args.command == "refresh":
            _verify_remote_registry(pair, args.gh)
            refreshed, changed = refresh_archive_states(
                pair,
                catalog,
                gh_command=args.gh,
            )
            print(
                f"refreshed catalog generation {refreshed.catalog_generation}: "
                f"{changed} archive-state change(s)"
            )
            return 0
        if not args.skip_github:
            try:
                _verify_remote_registry(pair, args.gh)
                lookup = _entry_lookup(pair)
                for catalog_entry in catalog.repositories:
                    expected_visibility, registry_entry = lookup[
                        catalog_entry.repository_id
                    ]
                    _verify_remote_entry(
                        expected_visibility,
                        registry_entry,
                        catalog_entry.lifecycle,
                        args.gh,
                    )
            except MaterializerError as exc:
                if args.command == "plan" and not args.show_slugs:
                    raise MaterializerError(
                        "hosted repository verification failed during plan"
                    ) from exc
                raise
        if args.command == "plan":
            for operation in plan_operations(
                pair,
                catalog,
                root,
                show_slugs=args.show_slugs,
            ):
                print(operation)
            return 0
        if args.command == "materialize":
            materialize(
                pair,
                catalog,
                root,
                clone_protocol=args.clone_protocol,
                gh_command=args.gh,
                skip_github=args.skip_github,
            )
            print("portfolio materialization complete")
            return 0
        if args.command == "sync":
            synchronize(pair, catalog, root)
            print("portfolio fetch-only synchronization complete")
            return 0
        if args.command == "audit":
            audit_catalog(pair, catalog, root)
            print("portfolio catalog audit passed")
            return 0
        raise MaterializerError(f"unsupported command: {args.command}")
    except MaterializerError as exc:
        if args.command == "plan" and not getattr(args, "show_slugs", False):
            print(
                "error: plan failed without exposing sensitive repository details; "
                "rerun with --show-slugs for local diagnosis",
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
