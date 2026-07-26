#!/usr/bin/env python3
"""Produce a read-only repository dependency and lifecycle review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

import portfolio_materializer
import repository_visibility


SCHEMA_VERSION = 1
MAX_SCAN_FILE_BYTES = 1024 * 1024
MAX_TRACKED_FILES = 100_000
PLAN_ROOT_KEYS = {"schema_version", "actions"}
PLAN_ACTION_KEYS = {"action", "target_repository_id", "reason"}
PLAN_DEPENDENCY_ACTION_KEYS = PLAN_ACTION_KEYS | {"dependency_repository_id"}
ACTION_NAMES = {"make-private", "archive", "retire", "remove-dependency"}

MANIFEST_NAMES = {
    ".gitmodules",
    "build.gradle",
    "build.gradle.kts",
    "bun.lock",
    "bun.lockb",
    "cargo.lock",
    "cargo.toml",
    "cmakelists.txt",
    "composer.json",
    "composer.lock",
    "conanfile.py",
    "conanfile.txt",
    "deps.edn",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "gradle.lockfile",
    "mix.exs",
    "mix.lock",
    "package-lock.json",
    "package.json",
    "package.resolved",
    "package.swift",
    "pipfile",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pom.xml",
    "project.clj",
    "pubspec.lock",
    "pubspec.yaml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "vcpkg.json",
    "yarn.lock",
}


class ReviewError(Exception):
    """Raised when a lifecycle review cannot be produced safely."""


class DirtyCheckoutError(ReviewError):
    """Raised when tracked checkout state is not an exact HEAD projection."""


class ConcurrentSnapshotError(ReviewError):
    """Raised when repository control state changes during evidence capture."""


@dataclass(frozen=True)
class ProposedAction:
    action: str
    target_repository_id: str
    reason: str
    dependency_repository_id: str | None = None


@dataclass(frozen=True)
class ScanResult:
    repositories: tuple[dict[str, Any], ...]
    manifests: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    blockers: tuple[dict[str, str], ...]
    warnings: tuple[dict[str, str], ...]
    snapshots: tuple[_RepositorySnapshot, ...] = ()


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    object_type: str
    object_id: str
    size: int | None
    path: str


@dataclass(frozen=True)
class _RepositorySnapshot:
    repository_id: str
    checkout: Path
    head: str
    index: bytes
    tree: tuple[_TreeEntry, ...]
    object_format: str
    control: bytes


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    label: str,
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise ReviewError(f"{label} has invalid keys ({'; '.join(details)})")


def _run(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    text: bool = True,
    input_data: str | bytes | None = None,
) -> subprocess.CompletedProcess[Any]:
    actual_command = list(command)
    environment: dict[str, str] | None = None
    if actual_command and actual_command[0] == "git":
        actual_command.insert(1, "--no-replace-objects")
        environment = os.environ.copy()
        for key in tuple(environment):
            if (
                key.startswith("GIT_CONFIG_KEY_")
                or key.startswith("GIT_CONFIG_VALUE_")
                or key.startswith("GIT_TRACE")
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
            ("core.attributesFile", os.devnull),
            ("core.fsmonitor", "false"),
            ("core.hooksPath", os.devnull),
            ("core.pager", "cat"),
            ("core.untrackedCache", "false"),
            ("fetch.recurseSubmodules", "false"),
            ("fetch.writeCommitGraph", "false"),
            ("maintenance.auto", "false"),
            ("submodule.recurse", "false"),
        )
        for index, (key, value) in enumerate(hardened_config):
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
        environment.update(
            {
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": str(len(hardened_config)),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
    try:
        return subprocess.run(
            actual_command,
            check=False,
            capture_output=True,
            text=text,
            timeout=timeout,
            input=input_data,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"cannot run read-only command {command[0]!r}: {exc}") from exc


def _nearest_existing_directory(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    if candidate.is_file():
        return candidate.parent
    return candidate


def _require_ignored_untracked(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    anchor = _nearest_existing_directory(absolute.parent)
    result = _run(["git", "-C", str(anchor), "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return
    worktree = Path(os.path.realpath(result.stdout.strip()))
    canonical = Path(os.path.realpath(absolute.parent)) / absolute.name
    try:
        relative = canonical.relative_to(worktree)
    except ValueError as exc:
        raise ReviewError(
            f"cannot establish {label} path containment in Git worktree: {path}"
        ) from exc
    tracked = _run(
        [
            "git",
            "-C",
            str(worktree),
            "ls-files",
            "--error-unmatch",
            "--",
            str(relative),
        ]
    )
    if tracked.returncode == 0:
        raise ReviewError(f"{label} must not be tracked by Git: {path}")
    ignored = _run(
        [
            "git",
            "-C",
            str(worktree),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            str(relative),
        ]
    )
    if ignored.returncode != 0:
        raise ReviewError(f"{label} inside a Git worktree must be ignored: {path}")


def _read_owner_only_json(path: Path, label: str) -> Any:
    try:
        raw = repository_visibility._read_secure_regular_file(path)
        _require_ignored_untracked(path, label)
        return json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except repository_visibility.RegistryError as exc:
        raise ReviewError(str(exc)) from exc
    except UnicodeError as exc:
        raise ReviewError(f"{label} is not valid UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewError(
            f"invalid JSON in {label} {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def _registry_entries_by_id(
    pair: repository_visibility.RegistryPair,
) -> dict[str, tuple[str, repository_visibility.RepositoryEntry]]:
    return {
        entry.repository_id: (visibility, entry)
        for visibility, entry in pair.entries
    }


def load_plan(
    path: Path,
    pair: repository_visibility.RegistryPair,
) -> tuple[ProposedAction, ...]:
    payload = _read_owner_only_json(path, "lifecycle plan")
    if type(payload) is not dict:
        raise ReviewError("lifecycle plan root must be a JSON object")
    _require_exact_keys(payload, PLAN_ROOT_KEYS, "lifecycle plan")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ReviewError("lifecycle plan must use schema_version 1")
    raw_actions = payload["actions"]
    if type(raw_actions) is not list:
        raise ReviewError("lifecycle plan actions must be a JSON array")

    registered = _registry_entries_by_id(pair)
    actions: list[ProposedAction] = []
    seen: set[tuple[str, str, str | None]] = set()
    for index, raw_action in enumerate(raw_actions):
        label = f"lifecycle plan action {index}"
        if type(raw_action) is not dict:
            raise ReviewError(f"{label} must be a JSON object")
        action_name = raw_action.get("action")
        if action_name == "remove-dependency":
            _require_exact_keys(raw_action, PLAN_DEPENDENCY_ACTION_KEYS, label)
        else:
            _require_exact_keys(raw_action, PLAN_ACTION_KEYS, label)
        if type(action_name) is not str or action_name not in ACTION_NAMES:
            raise ReviewError(
                f"{label} action must be one of {', '.join(sorted(ACTION_NAMES))}"
            )

        target_id = raw_action["target_repository_id"]
        if type(target_id) is not str:
            raise ReviewError(f"{label} target_repository_id must be a string")
        try:
            repository_visibility._validate_repository_id(target_id)
        except repository_visibility.RegistryError as exc:
            raise ReviewError(f"{label}: {exc}") from exc
        if target_id not in registered:
            raise ReviewError(f"{label} target_repository_id is not registered")

        reason = raw_action["reason"]
        if type(reason) is not str or not reason.strip() or reason != reason.strip():
            raise ReviewError(f"{label} reason must be a nonempty trimmed string")

        dependency_id: str | None = None
        if action_name == "remove-dependency":
            dependency_id = raw_action["dependency_repository_id"]
            if type(dependency_id) is not str:
                raise ReviewError(
                    f"{label} dependency_repository_id must be a string"
                )
            try:
                repository_visibility._validate_repository_id(dependency_id)
            except repository_visibility.RegistryError as exc:
                raise ReviewError(f"{label}: {exc}") from exc
            if dependency_id not in registered:
                raise ReviewError(
                    f"{label} dependency_repository_id is not registered"
                )
            if dependency_id == target_id:
                raise ReviewError(f"{label} cannot remove a self-dependency")

        identity = (action_name, target_id, dependency_id)
        if identity in seen:
            raise ReviewError(f"{label} duplicates an earlier proposed action")
        seen.add(identity)
        actions.append(
            ProposedAction(
                action=action_name,
                target_repository_id=target_id,
                dependency_repository_id=dependency_id,
                reason=reason,
            )
        )
    return tuple(actions)


def _issue(
    code: str,
    message: str,
    repository_id: str | None = None,
) -> dict[str, str]:
    result = {"code": code, "message": message}
    if repository_id is not None:
        result["repository_id"] = repository_id
    return result


def _is_manifest(path: str) -> bool:
    posix_path = PurePosixPath(path)
    name = posix_path.name.casefold()
    if path == ".gitmodules":
        return True
    if name == ".gitmodules":
        return False
    if name in MANIFEST_NAMES:
        return True
    if name.startswith("requirements-") and name.endswith(".txt"):
        return True
    if name.startswith("requirements.") and name.endswith(".txt"):
        return True
    if len(posix_path.parts) >= 3:
        lowered = tuple(part.casefold() for part in posix_path.parts)
        if lowered[-3] == "gradle" and lowered[-2] == "dependency-locks":
            return name.endswith(".lockfile")
    return False


def _path_has_symlink(root: Path, relative_path: str) -> bool:
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return False
    return False


def _safe_tree_path(raw_path: bytes) -> str:
    try:
        path = raw_path.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewError("tracked filename is not valid UTF-8") from exc
    pure_path = PurePosixPath(path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise ReviewError("tracked filename is not a safe relative POSIX path")
    return path


def _local_config_snapshot(checkout: Path) -> bytes:
    result = _run(
        [
            "git",
            "-C",
            str(checkout),
            "config",
            "--local",
            "--no-includes",
            "--null",
            "--list",
        ],
        text=False,
    )
    if result.returncode != 0:
        raise ReviewError("cannot read repository-local Git configuration")
    return result.stdout


def _verify_checkout_identity(
    checkout: Path,
    expected_entry: repository_visibility.RepositoryEntry,
) -> bytes:
    for control_path in (
        checkout / ".git",
        checkout / ".git" / "objects",
    ):
        try:
            metadata = control_path.lstat()
        except OSError as exc:
            raise ReviewError("repository Git control directory is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ReviewError("repository Git control directory is not a real directory")
    for control_file in (
        checkout / ".git" / "HEAD",
        checkout / ".git" / "config",
        checkout / ".git" / "index",
    ):
        try:
            metadata = control_file.lstat()
        except OSError as exc:
            raise ReviewError("repository Git control file is unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ReviewError("repository Git control file is not a real regular file")
    alternates = checkout / ".git" / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise ReviewError("repository object alternates are not allowed")
    grafts = checkout / ".git" / "info" / "grafts"
    if grafts.exists() or grafts.is_symlink():
        raise ReviewError("repository graft replacements are not allowed")

    local_configuration = _local_config_snapshot(checkout)
    configuration: list[tuple[str, str]] = []
    for record in local_configuration.split(b"\0"):
        if not record:
            continue
        try:
            raw_key, raw_value = record.split(b"\n", 1)
            configuration.append(
                (raw_key.decode("utf-8"), raw_value.decode("utf-8"))
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReviewError("repository-local Git configuration is malformed") from exc
    lowered_configuration_keys = {key.casefold() for key, _ in configuration}
    if "include.path" in lowered_configuration_keys or any(
        key.startswith("includeif.") and key.endswith(".path")
        for key in lowered_configuration_keys
    ):
        raise ReviewError("repository-local Git configuration includes are not allowed")
    if "core.worktree" in lowered_configuration_keys:
        raise ReviewError("repository-local core.worktree redirection is not allowed")
    false_values = {"0", "false", "no", "off"}
    if any(
        key.casefold() == "extensions.worktreeconfig"
        and value.casefold() not in false_values
        for key, value in configuration
    ):
        raise ReviewError("repository worktree-specific configuration is not allowed")

    top_level = _run(
        ["git", "-C", str(checkout), "rev-parse", "--show-toplevel"]
    )
    git_directory = _run(
        ["git", "-C", str(checkout), "rev-parse", "--absolute-git-dir"]
    )
    if (
        top_level.returncode != 0
        or git_directory.returncode != 0
        or not top_level.stdout.strip()
        or not git_directory.stdout.strip()
    ):
        raise ReviewError("checkout is not a verifiable Git worktree")
    if Path(top_level.stdout.strip()).resolve() != checkout.resolve():
        raise ReviewError("checkout target is nested in another Git worktree")
    expected_git_directory = (checkout / ".git").resolve()
    if Path(git_directory.stdout.strip()).resolve() != expected_git_directory:
        raise ReviewError("checkout is not a standalone Git repository")

    remote_names: set[str] = set()
    remote_urls: list[str] = []
    for key, value in configuration:
        match = re.fullmatch(r"remote\.(.+)\.(?:url|pushurl)", key)
        if match is None:
            continue
        remote_names.add(match.group(1))
        remote_urls.append(value)
    if "origin" not in remote_names or not remote_urls:
        raise ReviewError("checkout has no usable origin remote")
    expected_slug = expected_entry.slug.casefold()
    for remote_url in remote_urls:
        observed_slug = repository_visibility._normalize_github_remote(remote_url)
        if observed_slug is None or observed_slug.casefold() != expected_slug:
            raise ReviewError("checkout remote identity does not match the registry")

    shallow = _run(
        ["git", "-C", str(checkout), "rev-parse", "--is-shallow-repository"]
    )
    if shallow.returncode != 0 or shallow.stdout.strip().casefold() != "false":
        raise ReviewError("shallow checkout is not allowed")
    if any(
        key.casefold() in {"core.sparsecheckout", "index.sparse"}
        and value.casefold() not in false_values
        for key, value in configuration
    ):
        raise ReviewError("sparse checkout is not allowed")
    if any(
        (
            key.casefold() == "extensions.partialclone"
            and bool(value)
        )
        or (
            key.casefold().startswith("remote.")
            and key.casefold().endswith(".promisor")
            and value.casefold() not in false_values
        )
        for key, value in configuration
    ):
        raise ReviewError("partial clone is not allowed")
    if any(
        key.casefold().startswith("remote.")
        and key.casefold().endswith(".partialclonefilter")
        and bool(value)
        for key, value in configuration
    ):
        raise ReviewError("partial clone is not allowed")
    pack_directory = checkout / ".git" / "objects" / "pack"
    if pack_directory.is_symlink():
        raise ReviewError("repository pack directory must not be a symlink")
    if pack_directory.is_dir() and any(pack_directory.glob("*.promisor")):
        raise ReviewError("partial clone is not allowed")
    return local_configuration


def _parse_index_snapshot(raw: bytes) -> tuple[tuple[str, str, str], ...]:
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if len(records) > MAX_TRACKED_FILES:
        raise ReviewError(
            f"tracked file count exceeds the {MAX_TRACKED_FILES}-file safety limit"
        )
    entries: list[tuple[str, str, str]] = []
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            tag, mode, object_id, stage = metadata.decode("ascii").split()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReviewError("Git index contains a malformed tracked entry") from exc
        if tag != "H":
            raise DirtyCheckoutError(
                "Git index uses assume-unchanged, skip-worktree, or an unsafe stage"
            )
        if stage != "0":
            raise DirtyCheckoutError("Git index contains an unmerged tracked entry")
        path = _safe_tree_path(raw_path)
        entries.append((mode, object_id.lower(), path))
    return tuple(entries)


def _parse_head_tree(raw: bytes, object_format: str) -> tuple[_TreeEntry, ...]:
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if len(records) > MAX_TRACKED_FILES:
        raise ReviewError(
            f"tracked file count exceeds the {MAX_TRACKED_FILES}-file safety limit"
        )
    object_id_length = 40 if object_format == "sha1" else 64
    entries: list[_TreeEntry] = []
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, raw_object_id, raw_size = metadata.split()
            mode_text = mode.decode("ascii")
            type_text = object_type.decode("ascii")
            object_id = raw_object_id.decode("ascii").lower()
            size = None if raw_size == b"-" else int(raw_size, 10)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReviewError("HEAD tree contains a malformed tracked entry") from exc
        if (
            len(object_id) != object_id_length
            or re.fullmatch(r"[0-9a-f]+", object_id) is None
        ):
            raise ReviewError("HEAD tree contains an invalid object ID")
        path = _safe_tree_path(raw_path)
        entries.append(
            _TreeEntry(
                mode=mode_text,
                object_type=type_text,
                object_id=object_id,
                size=size,
                path=path,
            )
        )
    return tuple(entries)


def _capture_repository_snapshot(
    checkout: Path,
    expected_entry: repository_visibility.RepositoryEntry,
) -> _RepositorySnapshot:
    control = _verify_checkout_identity(checkout, expected_entry)
    head = _run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", "HEAD^{commit}"]
    )
    object_format = _run(
        ["git", "-C", str(checkout), "rev-parse", "--show-object-format"]
    )
    if (
        head.returncode != 0
        or re.fullmatch(r"[0-9a-fA-F]{40,64}", head.stdout.strip()) is None
    ):
        raise ReviewError("checkout has no verifiable HEAD commit")
    object_format_value = object_format.stdout.strip().casefold()
    if object_format.returncode != 0 or object_format_value not in {"sha1", "sha256"}:
        raise ReviewError("checkout uses an unsupported Git object format")
    index_result = _run(
        ["git", "-C", str(checkout), "ls-files", "-v", "--stage", "-z", "--"],
        text=False,
    )
    tree_result = _run(
        [
            "git",
            "-C",
            str(checkout),
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            "--long",
            head.stdout.strip(),
        ],
        text=False,
    )
    if index_result.returncode != 0:
        raise ReviewError("cannot enumerate the Git index")
    if tree_result.returncode != 0:
        raise ReviewError("cannot enumerate the immutable HEAD tree")
    index_entries = _parse_index_snapshot(index_result.stdout)
    tree = _parse_head_tree(tree_result.stdout, object_format_value)
    tree_index_entries = tuple(
        (entry.mode, entry.object_id, entry.path) for entry in tree
    )
    if index_entries != tree_index_entries:
        raise DirtyCheckoutError("Git index does not exactly match HEAD")
    return _RepositorySnapshot(
        repository_id=expected_entry.repository_id,
        checkout=checkout,
        head=head.stdout.strip().lower(),
        index=index_result.stdout,
        tree=tree,
        object_format=object_format_value,
        control=control,
    )


def _hash_blob_bytes(object_format: str, content: bytes) -> str:
    digest = hashlib.new(object_format)
    digest.update(f"blob {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def _worktree_entry_object_id(
    checkout: Path,
    entry: _TreeEntry,
    object_format: str,
) -> tuple[str | None, str | None]:
    parts = PurePosixPath(entry.path).parts
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    descriptors: list[int] = []
    try:
        descriptor = os.open(checkout, directory_flags)
        descriptors.append(descriptor)
        for part in parts[:-1]:
            descriptor = os.open(
                part,
                directory_flags,
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
        parent_descriptor = descriptors[-1]
        metadata = os.stat(
            parts[-1],
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if entry.mode == "120000":
            if not stat.S_ISLNK(metadata.st_mode):
                return None, "tracked symlink is missing or has changed type"
            target = os.readlink(parts[-1], dir_fd=parent_descriptor)
            return _hash_blob_bytes(object_format, os.fsencode(target)), None
        if entry.mode not in {"100644", "100755"}:
            return None, "tracked gitlink or special entry cannot be safely verified"
        if not stat.S_ISREG(metadata.st_mode):
            return None, "tracked file is missing or has changed type"
        expected_executable = entry.mode == "100755"
        if bool(metadata.st_mode & 0o111) != expected_executable:
            return None, "tracked file executable mode differs from HEAD"
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        file_descriptor = os.open(
            parts[-1],
            flags,
            dir_fd=parent_descriptor,
        )
        descriptors.append(file_descriptor)
        opened = os.fstat(file_descriptor)
        before = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        digest = hashlib.new(object_format)
        digest.update(f"blob {opened.st_size}\0".encode("ascii"))
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after_metadata = os.fstat(file_descriptor)
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
            after_metadata.st_ctime_ns,
        )
        if before != after:
            return None, "tracked file changed while its cleanliness was verified"
        return digest.hexdigest(), None
    except (FileNotFoundError, NotADirectoryError):
        return None, "tracked file is missing from the worktree"
    except OSError:
        return None, "tracked file cannot be safely read for cleanliness verification"
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _verify_worktree_matches_snapshot(snapshot: _RepositorySnapshot) -> None:
    for entry in snapshot.tree:
        observed_object_id, error = _worktree_entry_object_id(
            snapshot.checkout,
            entry,
            snapshot.object_format,
        )
        if error is not None or observed_object_id != entry.object_id:
            raise DirtyCheckoutError(error or "tracked working-tree bytes differ from HEAD")


def _read_tree_blobs(
    snapshot: _RepositorySnapshot,
) -> dict[str, tuple[str, bytes | None, int | None]]:
    results: dict[str, tuple[str, bytes | None, int | None]] = {}
    pending: list[_TreeEntry] = []
    for entry in snapshot.tree:
        if entry.mode == "120000":
            results[entry.path] = ("symlink", None, entry.size)
        elif entry.mode not in {"100644", "100755"} or entry.object_type != "blob":
            results[entry.path] = ("non-regular", None, entry.size)
        elif entry.size is None:
            results[entry.path] = ("unreadable", None, None)
        elif entry.size > MAX_SCAN_FILE_BYTES:
            results[entry.path] = ("oversized", None, entry.size)
        else:
            pending.append(entry)

    batches: list[list[_TreeEntry]] = []
    batch: list[_TreeEntry] = []
    batch_bytes = 0
    for entry in pending:
        entry_bytes = entry.size or 0
        if batch and (len(batch) >= 1024 or batch_bytes + entry_bytes > 16 * 1024 * 1024):
            batches.append(batch)
            batch = []
            batch_bytes = 0
        batch.append(entry)
        batch_bytes += entry_bytes
    if batch:
        batches.append(batch)

    for batch in batches:
        query = b"".join(
            entry.object_id.encode("ascii") + b"\n" for entry in batch
        )
        response = _run(
            ["git", "-C", str(snapshot.checkout), "cat-file", "--batch"],
            text=False,
            input_data=query,
        )
        if response.returncode != 0:
            raise ReviewError("cannot read immutable tracked blobs from Git objects")
        cursor = 0
        for entry in batch:
            header_end = response.stdout.find(b"\n", cursor)
            if header_end < 0:
                raise ReviewError("Git returned a truncated immutable blob response")
            header = response.stdout[cursor:header_end].split()
            if len(header) != 3:
                raise ReviewError("Git could not resolve an immutable tracked blob")
            try:
                returned_id = header[0].decode("ascii").lower()
                object_type = header[1].decode("ascii")
                size = int(header[2], 10)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ReviewError("Git returned malformed immutable blob metadata") from exc
            cursor = header_end + 1
            content_end = cursor + size
            if content_end >= len(response.stdout):
                raise ReviewError("Git returned truncated immutable tracked bytes")
            content = response.stdout[cursor:content_end]
            if response.stdout[content_end : content_end + 1] != b"\n":
                raise ReviewError("Git returned malformed immutable blob framing")
            cursor = content_end + 1
            if (
                returned_id != entry.object_id
                or object_type != "blob"
                or size != entry.size
                or _hash_blob_bytes(snapshot.object_format, content)
                != entry.object_id
            ):
                raise ReviewError("immutable tracked blob metadata changed unexpectedly")
            results[entry.path] = ("read", content, size)
        if cursor != len(response.stdout):
            raise ReviewError("Git returned unexpected immutable blob output")
    return results


def _revalidate_repository_snapshot(
    snapshot: _RepositorySnapshot,
    expected_entry: repository_visibility.RepositoryEntry,
) -> None:
    current = _capture_repository_snapshot(snapshot.checkout, expected_entry)
    if current != snapshot:
        raise ConcurrentSnapshotError(
            "repository HEAD, index, or Git control state changed during review"
        )
    _verify_worktree_matches_snapshot(current)


def _checkout_path(
    portfolio_root: Path,
    relative_path: str,
) -> tuple[Path | None, str | None]:
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        return None, "catalog path is not a safe relative POSIX path"
    if _path_has_symlink(portfolio_root, relative_path):
        return None, "catalog path traverses a symlink"
    checkout = portfolio_root.joinpath(*pure_path.parts)
    try:
        checkout.resolve(strict=False).relative_to(portfolio_root)
    except ValueError:
        return None, "catalog path escapes the portfolio root"
    return checkout, None


def _compile_slug_pattern(
    pair: repository_visibility.RegistryPair,
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    by_slug = {
        entry.slug.casefold(): entry.repository_id for _, entry in pair.entries
    }
    if not by_slug:
        return None, by_slug
    alternatives = "|".join(
        re.escape(slug)
        for slug in sorted(by_slug, key=lambda value: (-len(value), value))
    )
    return (
        re.compile(
            rf"(?<![A-Za-z0-9._-])(?P<slug>{alternatives})"
            rf"(?:\.git)?(?![A-Za-z0-9._-])",
            re.IGNORECASE,
        ),
        by_slug,
    )


def scan_portfolio(
    pair: repository_visibility.RegistryPair,
    catalog: portfolio_materializer.CatalogDocument,
    portfolio_root: Path,
) -> ScanResult:
    if not portfolio_root.is_dir():
        raise ReviewError(f"portfolio root is not a directory: {portfolio_root}")
    portfolio_root = portfolio_root.resolve()
    pattern, repository_id_by_slug = _compile_slug_pattern(pair)
    registered = _registry_entries_by_id(pair)
    repository_reports: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    stable_snapshots: dict[
        str,
        tuple[_RepositorySnapshot, repository_visibility.RepositoryEntry],
    ] = {}

    for entry in sorted(
        catalog.repositories,
        key=lambda value: (value.relative_path.casefold(), value.repository_id),
    ):
        repository_id = entry.repository_id
        checkout, path_error = _checkout_path(
            portfolio_root,
            entry.relative_path,
        )
        report: dict[str, Any] = {
            "repository_id": repository_id,
            "relative_path": entry.relative_path,
            "lifecycle": entry.lifecycle,
            "desired_presence": entry.desired_presence,
            "status": "unavailable",
            "source_commit": None,
            "tracked_file_count": None,
        }
        if path_error is not None:
            blockers.append(_issue("unsafe-checkout-path", path_error, repository_id))
            if entry.desired_presence == "absent":
                blockers.append(
                    _issue(
                        "absent-repository-evidence-unavailable",
                        "repository is configured absent, so its outgoing dependency "
                        "evidence is unavailable",
                        repository_id,
                    )
                )
                if "symlink" in path_error.casefold():
                    blockers.append(
                        _issue(
                            "configured-absent-path-present",
                            "configured-absent checkout path contains a symlink",
                            repository_id,
                        )
                    )
            repository_reports.append(report)
            continue
        assert checkout is not None

        if entry.desired_presence == "absent":
            report["status"] = "not-scanned-configured-absent"
            blockers.append(
                _issue(
                    "absent-repository-evidence-unavailable",
                    "repository is configured absent, so its outgoing dependency "
                    "evidence is unavailable",
                    repository_id,
                )
            )
            try:
                checkout.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                blockers.append(
                    _issue(
                        "checkout-path-inspection-failed",
                        f"cannot inspect configured-absent checkout path: {exc}",
                        repository_id,
                    )
                )
            else:
                blockers.append(
                    _issue(
                        "configured-absent-path-present",
                        "repository is configured absent but its checkout path exists",
                        repository_id,
                    )
                )
            repository_reports.append(report)
            continue

        if not checkout.exists():
            blockers.append(
                _issue(
                    "missing-checkout",
                    "configured checkout is absent",
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue
        if not checkout.is_dir():
            blockers.append(
                _issue(
                    "checkout-not-directory",
                    "configured checkout is not a directory",
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue
        git_control = checkout / ".git"
        if git_control.is_symlink():
            blockers.append(
                _issue(
                    "symlink-git-control",
                    "checkout .git control path is a symlink",
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue

        _, expected_entry = registered[repository_id]
        try:
            snapshot = _capture_repository_snapshot(checkout, expected_entry)
            _verify_worktree_matches_snapshot(snapshot)
        except DirtyCheckoutError as exc:
            report["status"] = "not-scanned-dirty-tracked"
            blockers.append(
                _issue(
                    "dirty-tracked-checkout",
                    "checkout index or tracked working-tree bytes do not exactly "
                    f"match HEAD: {exc}",
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue
        except ReviewError as exc:
            blockers.append(
                _issue(
                    "checkout-identity-mismatch",
                    str(exc),
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue
        report["source_commit"] = snapshot.head
        report["status"] = "scanned"
        local_manifests: list[dict[str, Any]] = []
        local_references: list[dict[str, Any]] = []
        local_blockers: list[dict[str, str]] = []
        local_warnings: list[dict[str, str]] = []
        try:
            blob_results = _read_tree_blobs(snapshot)
        except ReviewError as exc:
            blockers.append(
                _issue(
                    "tracked-inventory-failed",
                    str(exc),
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue
        report["tracked_file_count"] = len(snapshot.tree)

        for tree_entry in snapshot.tree:
            relative_file = tree_entry.path
            manifest = _is_manifest(relative_file)
            status, content, byte_count = blob_results[relative_file]
            if manifest:
                local_manifests.append(
                    {
                        "source_repository_id": repository_id,
                        "source_commit": report["source_commit"],
                        "file": relative_file,
                        "byte_count": byte_count,
                        "scan_status": status,
                    }
                )
            if status != "read":
                issue = _issue(
                    f"tracked-file-{status}",
                    f"tracked file was not scanned: {relative_file}",
                    repository_id,
                )
                if status in {"missing", "unsafe-path", "unreadable"}:
                    local_blockers.append(issue)
                else:
                    local_warnings.append(issue)
                continue
            assert content is not None
            if b"\0" in content:
                if manifest:
                    local_warnings.append(
                        _issue(
                            "binary-manifest",
                            f"dependency manifest is binary: {relative_file}",
                            repository_id,
                        )
                    )
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                if manifest:
                    local_warnings.append(
                        _issue(
                            "non-utf8-manifest",
                            f"dependency manifest is not UTF-8: {relative_file}",
                            repository_id,
                        )
                    )
                continue
            if pattern is None:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                matched_target_ids = {
                    repository_id_by_slug[match.group("slug").casefold()]
                    for match in pattern.finditer(line)
                }
                for target_id in sorted(matched_target_ids):
                    evidence = {
                        "source_repository_id": repository_id,
                        "source_commit": report["source_commit"],
                        "file": relative_file,
                        "line": line_number,
                        "target_repository_id": target_id,
                        "evidence": (
                            "dependency-manifest"
                            if manifest
                            else "tracked-text-reference"
                        ),
                    }
                    local_references.append(evidence)
                    source_visibility, _ = registered[repository_id]
                    target_visibility, _ = registered[target_id]
                    if (
                        source_visibility == "public"
                        and target_visibility == "private"
                    ):
                        local_blockers.append(
                            _issue(
                                "public-to-private-reference",
                                "public repository references private target "
                                f"{target_id} at {relative_file}:{line_number}; "
                                "publication and access boundaries require review",
                                repository_id,
                            )
                        )
        try:
            _revalidate_repository_snapshot(snapshot, expected_entry)
        except (ConcurrentSnapshotError, DirtyCheckoutError, ReviewError) as exc:
            report["status"] = "not-scanned-concurrent-change"
            report["source_commit"] = None
            report["tracked_file_count"] = None
            blockers.append(
                _issue(
                    "repository-changed-during-scan",
                    str(exc),
                    repository_id,
                )
            )
            repository_reports.append(report)
            continue
        manifests.extend(local_manifests)
        references.extend(local_references)
        blockers.extend(local_blockers)
        warnings.extend(local_warnings)
        stable_snapshots[repository_id] = (snapshot, expected_entry)
        repository_reports.append(report)

    catalog_paths = {
        portfolio_root.joinpath(
            *PurePosixPath(entry.relative_path).parts
        ).resolve(strict=False)
        for entry in catalog.repositories
    }
    for repository in repository_visibility._discover_git_repositories(
        portfolio_root
    ):
        if repository.resolve() in catalog_paths:
            continue
        relative_path = repository.resolve().relative_to(portfolio_root).as_posix()
        blockers.append(
            _issue(
                "unmanaged-local-checkout",
                f"local checkout is not bound to a registered repository: "
                f"{relative_path}",
            )
        )
        repository_reports.append(
            {
                "repository_id": None,
                "relative_path": relative_path,
                "lifecycle": "unmanaged",
                "desired_presence": "review",
                "status": "not-scanned-unmanaged",
                "source_commit": None,
                "tracked_file_count": None,
            }
        )

    for repository_id, (snapshot, expected_entry) in stable_snapshots.items():
        try:
            _revalidate_repository_snapshot(snapshot, expected_entry)
        except (ConcurrentSnapshotError, DirtyCheckoutError, ReviewError) as exc:
            raise ReviewError(
                "repository control snapshot changed before report assembly for "
                f"{repository_id}: {exc}"
            ) from exc

    references.sort(
        key=lambda value: (
            value["source_repository_id"],
            value["file"],
            value["line"],
            value["target_repository_id"],
        )
    )
    manifests.sort(
        key=lambda value: (
            value["source_repository_id"],
            value["file"],
        )
    )
    return ScanResult(
        repositories=tuple(repository_reports),
        manifests=tuple(manifests),
        references=tuple(references),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        snapshots=tuple(
            snapshot for snapshot, _ in stable_snapshots.values()
        ),
    )


def review_actions(
    actions: Iterable[ProposedAction],
    pair: repository_visibility.RegistryPair,
    scan: ScanResult,
) -> tuple[dict[str, Any], ...]:
    registered = _registry_entries_by_id(pair)
    repository_reports = {
        report["repository_id"]: report
        for report in scan.repositories
        if report["repository_id"] is not None
    }
    incomplete_coverage = bool(scan.blockers)
    results: list[dict[str, Any]] = []
    for action in actions:
        blockers: list[str] = []
        warnings: list[str] = []
        visibility, _ = registered[action.target_repository_id]
        target_report = repository_reports.get(action.target_repository_id)
        incoming = [
            reference
            for reference in scan.references
            if reference["target_repository_id"] == action.target_repository_id
            and reference["source_repository_id"] != action.target_repository_id
        ]
        if target_report is None:
            blockers.append(
                "target repository has no catalog-backed lifecycle scan result"
            )
        elif target_report["desired_presence"] == "absent":
            blockers.append(
                "target repository is configured absent and unavailable for review"
            )
        elif target_report["status"] != "scanned":
            blockers.append(
                "target repository is unavailable or was not safely scanned"
            )
        if incomplete_coverage:
            blockers.append(
                "portfolio scan coverage is incomplete; resolve report blockers first"
            )
        if action.action == "make-private":
            if visibility == "private":
                warnings.append("target is already classified private")
            if incoming:
                blockers.append(
                    f"{len(incoming)} incoming tracked reference(s) require consumer review"
                )
            warnings.append(
                "manual public-to-private review must cover access, forks, Pages, "
                "Actions, packages, releases, and integrations"
            )
        elif action.action in {"archive", "retire"}:
            if visibility == "public":
                blockers.append(
                    "target remains public; complete the explicit privacy review first"
                )
            if incoming:
                blockers.append(
                    f"{len(incoming)} incoming tracked reference(s) require removal "
                    "or an accepted migration"
                )
            warnings.append(
                "verify owners, releases, deployments, cooling period, and a "
                "restorable backup manually"
            )
        else:
            assert action.dependency_repository_id is not None
            matching = [
                reference
                for reference in scan.references
                if reference["source_repository_id"] == action.target_repository_id
                and reference["target_repository_id"]
                == action.dependency_repository_id
            ]
            if matching:
                warnings.append(
                    f"{len(matching)} tracked reference(s) identify candidate edits; "
                    "generated or runtime dependencies still require review"
                )
            else:
                warnings.append(
                    "no tracked slug reference was found; verify package names, "
                    "generated files, runtime configuration, and history manually"
                )

        result: dict[str, Any] = {
            "action": action.action,
            "target_repository_id": action.target_repository_id,
            "reason": action.reason,
            "disposition": "blocked" if blockers else "manual-review",
            "blockers": blockers,
            "warnings": warnings,
        }
        if action.dependency_repository_id is not None:
            result["dependency_repository_id"] = action.dependency_repository_id
        results.append(result)
    return tuple(results)


def build_report(
    pair: repository_visibility.RegistryPair,
    catalog: portfolio_materializer.CatalogDocument,
    portfolio_root: Path,
    scan: ScanResult,
    actions: tuple[ProposedAction, ...],
) -> dict[str, Any]:
    action_reviews = review_actions(actions, pair, scan)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "registry": {
            "registry_id": pair.registry_id,
            "generation": pair.generation,
        },
        "catalog": {
            "catalog_generation": catalog.catalog_generation,
            "portfolio_root": str(portfolio_root.resolve()),
        },
        "summary": {
            "repository_count": len(scan.repositories),
            "manifest_count": len(scan.manifests),
            "reference_count": len(scan.references),
            "blocker_count": len(scan.blockers),
            "warning_count": len(scan.warnings),
            "proposed_action_count": len(action_reviews),
            "blocked_action_count": sum(
                review["disposition"] == "blocked" for review in action_reviews
            ),
        },
        "repositories": list(scan.repositories),
        "dependency_manifests": list(scan.manifests),
        "repository_references": list(scan.references),
        "coverage": {
            "blockers": list(scan.blockers),
            "warnings": list(scan.warnings),
        },
        "proposed_actions": list(action_reviews),
        "safety": {
            "changes_applied": False,
            "github_mutations": False,
            "git_mutations": False,
        },
    }


def _revalidate_scan_result(
    scan: ScanResult,
    pair: repository_visibility.RegistryPair,
) -> None:
    registered = _registry_entries_by_id(pair)
    for snapshot in scan.snapshots:
        registered_entry = registered.get(snapshot.repository_id)
        if registered_entry is None:
            raise ReviewError(
                "registered repository identity changed before report assembly"
            )
        _, expected_entry = registered_entry
        _revalidate_repository_snapshot(snapshot, expected_entry)


def _ensure_owner_only_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ReviewError(
            f"cannot create lifecycle output directory {path}: {exc}"
        ) from exc

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ReviewError(
                f"lifecycle output parent is not a directory: {path}"
            )
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ReviewError(
                f"lifecycle output parent is not owned by the current user: {path}"
            )
        os.fchmod(descriptor, 0o700)
        secured = os.fstat(descriptor)
        if stat.S_IMODE(secured.st_mode) != 0o700:
            raise ReviewError(
                f"lifecycle output parent is not owner-only mode 0700: {path}"
            )
    except ReviewError:
        raise
    except OSError as exc:
        raise ReviewError(
            f"cannot secure lifecycle output directory {path}: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_owner_only_json(path: Path, payload: dict[str, Any]) -> None:
    _require_ignored_untracked(path, "lifecycle review output")
    _ensure_owner_only_directory(path.parent)
    if path.exists():
        try:
            repository_visibility._read_secure_regular_file(path)
        except repository_visibility.RegistryError as exc:
            raise ReviewError(str(exc)) from exc
    temporary_path = path.parent / (
        f".{path.name}.{os.getpid()}.tmp.local.json"
    )
    _require_ignored_untracked(temporary_path, "lifecycle review temporary output")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    temporary_created = False
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        serialized = (
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        offset = 0
        while offset < len(serialized):
            written = os.write(descriptor, serialized[offset:])
            if written <= 0:
                raise OSError("short write while serializing lifecycle review")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise ReviewError(f"cannot write lifecycle review output {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_created:
            temporary_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan tracked repository text for lifecycle dependency evidence "
            "without executing repository content or applying changes."
        )
    )
    parser.add_argument("--private", required=True, help="private registry JSON")
    parser.add_argument("--public", required=True, help="public registry JSON")
    parser.add_argument("--catalog", required=True, help="portfolio catalog JSON")
    parser.add_argument("--portfolio-root", required=True, help="checkout root")
    parser.add_argument("--output", required=True, help="ignored owner-only report JSON")
    parser.add_argument(
        "--plan",
        help="optional ignored owner-only lifecycle proposal JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output_path = Path(arguments.output)
    protected_inputs = {
        Path(arguments.private).resolve(strict=False),
        Path(arguments.public).resolve(strict=False),
        Path(arguments.catalog).resolve(strict=False),
    }
    if arguments.plan:
        protected_inputs.add(Path(arguments.plan).resolve(strict=False))
    if output_path.resolve(strict=False) in protected_inputs:
        print("error: lifecycle review output must differ from every input", file=sys.stderr)
        return 2
    try:
        _require_ignored_untracked(output_path, "lifecycle review output")
        _require_ignored_untracked(Path(arguments.private), "private registry")
        _require_ignored_untracked(Path(arguments.public), "public registry")
        _require_ignored_untracked(Path(arguments.catalog), "portfolio catalog")
        pair = repository_visibility.load_pair(arguments.private, arguments.public)
        catalog = portfolio_materializer.load_catalog(arguments.catalog, pair)
        actions = (
            load_plan(Path(arguments.plan), pair)
            if arguments.plan
            else ()
        )
        scan = scan_portfolio(pair, catalog, Path(arguments.portfolio_root))
        _require_ignored_untracked(Path(arguments.private), "private registry")
        _require_ignored_untracked(Path(arguments.public), "public registry")
        _require_ignored_untracked(Path(arguments.catalog), "portfolio catalog")
        current_pair = repository_visibility.load_pair(
            arguments.private,
            arguments.public,
        )
        if current_pair != pair:
            raise ReviewError(
                "visibility registry changed during lifecycle evidence capture"
            )
        current_catalog = portfolio_materializer.load_catalog(
            arguments.catalog,
            current_pair,
        )
        if current_catalog != catalog:
            raise ReviewError(
                "portfolio catalog changed during lifecycle evidence capture"
            )
        current_actions = (
            load_plan(Path(arguments.plan), current_pair)
            if arguments.plan
            else ()
        )
        if current_actions != actions:
            raise ReviewError(
                "lifecycle plan changed during lifecycle evidence capture"
            )
        _revalidate_scan_result(scan, current_pair)
        report = build_report(
            current_pair,
            current_catalog,
            Path(arguments.portfolio_root),
            scan,
            current_actions,
        )
        _write_owner_only_json(output_path, report)
    except (
        ReviewError,
        repository_visibility.RegistryError,
        portfolio_materializer.MaterializerError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        "review complete: "
        f"{len(scan.repositories)} repositories, "
        f"{len(scan.references)} registered references, "
        f"{len(scan.blockers)} coverage blockers, "
        f"{report['summary']['blocked_action_count']} blocked proposed actions"
    )
    return 1 if scan.blockers or report["summary"]["blocked_action_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
