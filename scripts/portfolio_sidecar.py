#!/usr/bin/env python3
"""Back up selected ignored data without mixing it into public Git history.

This schema-v1 runtime is deliberately a standalone, single-writer
coordinator. Mesh nodes are storage replicas only; automatic coordinator
failover requires the future quorum authority and fencing-token integration.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

import portfolio_materializer as materializer
import repository_visibility as visibility


SCHEMA_VERSION = 1
COORDINATOR_MODE = "standalone-no-automatic-failover"
TIERS = {"hosted-encrypted", "mesh-only"}
ADAPTER = "filesystem-static"
MAX_CONFIG_ITEMS = 10_000
MAX_RESTIC_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_SECRET_BYTES = 1024 * 1024
MAX_DATASET_FILES = 100_000
MAX_DATASET_BYTES = 100 * 1024 * 1024 * 1024
MAX_LIMIT = (1 << 63) - 1
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
RESTIC_SNAPSHOT_RE = re.compile(r"[0-9a-f]{64}")
RESTIC_COMMAND_TOKEN_RE = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")
_PROCESS_RUNNER_LOCK = threading.Lock()
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_DARWIN_WAITID: Any | None = None
POLICY_ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "registry_generation",
    "policy_generation",
    "datasets",
}
DATASET_KEYS = {
    "dataset_id",
    "repository_id",
    "selectors",
    "tier",
    "adapter",
    "max_files",
    "max_total_bytes",
    "target_set_id",
}
TARGET_ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "registry_generation",
    "target_generation",
    "target_sets",
}
TARGET_SET_KEYS = {"target_set_id", "tier", "required_acks", "targets"}
TARGET_KEYS = {
    "target_id",
    "repository_file",
    "password_file",
    "identity_file",
    "mesh_address",
    "failure_domain",
}
STATE_ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "registry_generation",
    "policy_generation",
    "policy_sha256",
    "target_generation",
    "target_sha256",
    "state_generation",
    "coordinator_mode",
    "datasets",
}
STATE_DATASET_KEYS = {
    "dataset_id",
    "repository_id",
    "sequence",
    "manifest_sha256",
    "file_count",
    "total_bytes",
    "committed_at",
    "replicas",
}
REPLICA_KEYS = {"target_id", "snapshot_id"}


class SidecarError(Exception):
    """Raised when sidecar configuration or capture state is unsafe."""


class _DarwinSigval(ctypes.Union):
    _fields_ = (("integer", ctypes.c_int), ("pointer", ctypes.c_void_p))


class _DarwinSiginfo(ctypes.Structure):
    _fields_ = (
        ("signo", ctypes.c_int),
        ("error", ctypes.c_int),
        ("code", ctypes.c_int),
        ("pid", ctypes.c_int),
        ("uid", ctypes.c_uint),
        ("status", ctypes.c_int),
        ("address", ctypes.c_void_p),
        ("value", _DarwinSigval),
        ("band", ctypes.c_long),
        ("padding", ctypes.c_ulong * 7),
    )


@dataclass(frozen=True)
class DatasetPolicy:
    dataset_id: str
    repository_id: str
    selectors: tuple[str, ...]
    tier: str
    adapter: str
    max_files: int
    max_total_bytes: int
    target_set_id: str


@dataclass(frozen=True)
class PolicyDocument:
    path: Path
    registry_id: str
    registry_generation: int
    policy_generation: int
    content_sha256: str
    datasets: tuple[DatasetPolicy, ...]


@dataclass(frozen=True)
class Target:
    target_id: str
    repository_file: Path
    password_file: Path
    identity_file: Path
    sftp_host: str
    sftp_user: str
    mesh_address: str | None
    failure_domain: str
    repository_sha256: str
    password_sha256: str
    identity_sha256: str


@dataclass(frozen=True)
class TargetSet:
    target_set_id: str
    tier: str
    required_acks: int
    targets: tuple[Target, ...]


@dataclass(frozen=True)
class StagedTarget:
    repository_file: Path
    password_file: Path
    identity_file: Path


@dataclass(frozen=True)
class KnownHostsFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class TargetsDocument:
    path: Path
    registry_id: str
    registry_generation: int
    target_generation: int
    content_sha256: str
    target_sets: tuple[TargetSet, ...]


@dataclass(frozen=True)
class ReplicaState:
    target_id: str
    snapshot_id: str


@dataclass(frozen=True)
class DatasetState:
    dataset_id: str
    repository_id: str
    sequence: int
    manifest_sha256: str | None
    file_count: int
    total_bytes: int
    committed_at: str | None
    replicas: tuple[ReplicaState, ...]


@dataclass(frozen=True)
class StateDocument:
    path: Path
    registry_id: str
    registry_generation: int
    policy_generation: int
    policy_sha256: str
    target_generation: int
    target_sha256: str
    state_generation: int
    coordinator_mode: str
    datasets: tuple[DatasetState, ...]


@dataclass(frozen=True)
class _CreatedPrivateFile:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    size: int
    mode: int
    uid: int
    gid: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class DatasetCapture:
    dataset_id: str
    checkout: Path
    files: tuple[FileSnapshot, ...]
    manifest_sha256: str
    total_bytes: int
    backup_root: Path | None = field(default=None, compare=False)


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    try:
        return visibility._object_without_duplicate_keys(pairs)
    except visibility.RegistryError as exc:
        raise SidecarError(str(exc)) from exc


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
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
    raise SidecarError(f"{label} has invalid keys ({'; '.join(details)})")


def _validate_id(value: object, label: str) -> str:
    if type(value) is not str or ID_RE.fullmatch(value) is None:
        raise SidecarError(f"{label} must be a stable opaque identifier")
    return value


def _validate_generation(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise SidecarError(f"{label} must be a non-negative integer")
    return value


def _validate_limit(value: object, label: str) -> int:
    if type(value) is not int or value <= 0 or value > MAX_LIMIT:
        raise SidecarError(f"{label} must be an integer from 1 through {MAX_LIMIT}")
    return value


def _canonical_document_hash(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _validate_selector(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise SidecarError("sidecar selector must be a non-empty trimmed string")
    if unicodedata.normalize("NFC", value) != value:
        raise SidecarError("sidecar selector must use NFC Unicode")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise SidecarError("sidecar selector contains a control character")
    if "\\" in value or any(character in value for character in "*?[]"):
        raise SidecarError("sidecar selectors are exact POSIX paths, not globs")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SidecarError("sidecar selector contains an unsafe path component")
    if any(part.casefold() == ".git" for part in parts):
        raise SidecarError("sidecar selector may not enter Git control data")
    pure = PurePosixPath(value)
    if pure.is_absolute() or str(pure) != value:
        raise SidecarError("sidecar selector must be a normalized relative path")
    return value


def _validate_private_parent(path: Path, label: str) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise SidecarError(f"{label} parent directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SidecarError(f"{label} parent must be a real directory")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise SidecarError(f"{label} parent is not owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SidecarError(f"{label} parent must not grant group or other access")


def _require_ignored_private_path(path: Path, label: str, *, exists: bool) -> None:
    if not path.is_absolute():
        raise SidecarError(f"{label} path must be absolute")
    _validate_private_parent(path, label)
    try:
        visibility._require_ignored_or_outside_git(path)
        if exists:
            metadata = path.lstat()
            if metadata.st_nlink != 1:
                raise SidecarError(f"{label} must not be hard-linked")
            visibility._read_secure_regular_file(path)
    except SidecarError:
        raise
    except visibility.RegistryError as exc:
        raise SidecarError(
            f"{label} does not satisfy the private ignored-file contract"
        ) from exc
    except OSError as exc:
        raise SidecarError(f"{label} is unavailable or insecure") from exc


def _read_private_json(path: Path, label: str) -> Any:
    _require_ignored_private_path(path, label, exists=True)
    try:
        raw = _read_stable_private_text(path, label)
        return json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except UnicodeError as exc:
        raise SidecarError(f"{label} is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SidecarError(
            f"invalid JSON in {label}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def _validate_absolute_secret_path(value: object, label: str) -> Path:
    if type(value) is not str or not value:
        raise SidecarError(f"{label} must be an absolute file path")
    path = Path(value)
    if not path.is_absolute() or path != path.resolve():
        raise SidecarError(f"{label} must be a canonical absolute file path")
    _require_ignored_private_path(path, label, exists=True)
    try:
        if path.lstat().st_nlink != 1:
            raise SidecarError(f"{label} must not be hard-linked")
    except OSError as exc:
        raise SidecarError(f"{label} cannot be inspected") from exc
    return path


def _read_secure_text_file(path: Path, label: str) -> str:
    value = _read_stable_private_text(path, label)
    if "\0" in value or not value.strip():
        raise SidecarError(f"{label} must contain a non-empty text value")
    return value


def load_policy(path: Path, pair: visibility.RegistryPair) -> PolicyDocument:
    payload = _read_private_json(path, "sidecar policy")
    if type(payload) is not dict:
        raise SidecarError("sidecar policy root must be a JSON object")
    _require_exact_keys(payload, POLICY_ROOT_KEYS, "sidecar policy")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SidecarError(f"sidecar policy must use schema_version {SCHEMA_VERSION}")
    if payload["registry_id"] != pair.registry_id:
        raise SidecarError("sidecar policy registry_id does not match the registry")
    registry_generation = _validate_generation(
        payload["registry_generation"], "sidecar policy registry_generation"
    )
    if registry_generation != pair.generation:
        raise SidecarError("sidecar policy registry generation is stale")
    policy_generation = _validate_generation(
        payload["policy_generation"], "sidecar policy generation"
    )
    raw_datasets = payload["datasets"]
    if type(raw_datasets) is not list or len(raw_datasets) > MAX_CONFIG_ITEMS:
        raise SidecarError("sidecar policy datasets must be a bounded JSON array")
    registry = {
        entry.repository_id: (entry_visibility, entry)
        for entry_visibility, entry in pair.entries
    }
    datasets: list[DatasetPolicy] = []
    seen_ids: set[str] = set()
    selector_owners: list[tuple[str, str, tuple[str, ...]]] = []
    for index, raw_dataset in enumerate(raw_datasets):
        label = f"sidecar dataset {index}"
        if type(raw_dataset) is not dict:
            raise SidecarError(f"{label} must be a JSON object")
        _require_exact_keys(raw_dataset, DATASET_KEYS, label)
        dataset_id = _validate_id(raw_dataset["dataset_id"], f"{label} dataset_id")
        if dataset_id in seen_ids:
            raise SidecarError("sidecar policy contains a duplicate dataset_id")
        seen_ids.add(dataset_id)
        try:
            repository_id = visibility._validate_repository_id(
                raw_dataset["repository_id"]
            )
        except visibility.RegistryError as exc:
            raise SidecarError(str(exc)) from exc
        registered = registry.get(repository_id)
        if registered is None:
            raise SidecarError(f"{label} repository_id is not registered")
        if registered[0] != "public":
            raise SidecarError(
                f"{label} must belong to a registry-public code repository"
            )
        raw_selectors = raw_dataset["selectors"]
        if (
            type(raw_selectors) is not list
            or not raw_selectors
            or len(raw_selectors) > MAX_CONFIG_ITEMS
        ):
            raise SidecarError(f"{label} selectors must be a non-empty bounded array")
        selectors = tuple(_validate_selector(value) for value in raw_selectors)
        expected_selectors = tuple(sorted(selectors, key=lambda value: value.casefold()))
        if selectors != expected_selectors or len({value.casefold() for value in selectors}) != len(selectors):
            raise SidecarError(f"{label} selectors must be unique and sorted")
        tier = raw_dataset["tier"]
        if tier not in TIERS:
            raise SidecarError(f"{label} has an unsupported tier")
        if raw_dataset["adapter"] != ADAPTER:
            raise SidecarError(f"{label} must use adapter {ADAPTER!r}")
        max_files = _validate_limit(raw_dataset["max_files"], f"{label} max_files")
        max_total_bytes = _validate_limit(
            raw_dataset["max_total_bytes"], f"{label} max_total_bytes"
        )
        if max_files > MAX_DATASET_FILES:
            raise SidecarError(
                f"{label} max_files exceeds the hard ceiling {MAX_DATASET_FILES}"
            )
        if max_total_bytes > MAX_DATASET_BYTES:
            raise SidecarError(
                f"{label} max_total_bytes exceeds the hard ceiling "
                f"{MAX_DATASET_BYTES}"
            )
        target_set_id = _validate_id(
            raw_dataset["target_set_id"], f"{label} target_set_id"
        )
        datasets.append(
            DatasetPolicy(
                dataset_id=dataset_id,
                repository_id=repository_id,
                selectors=selectors,
                tier=tier,
                adapter=ADAPTER,
                max_files=max_files,
                max_total_bytes=max_total_bytes,
                target_set_id=target_set_id,
            )
        )
        for selector in selectors:
            selector_owners.append(
                (
                    repository_id,
                    dataset_id,
                    tuple(part.casefold() for part in PurePosixPath(selector).parts),
                )
            )
    if datasets != sorted(datasets, key=lambda value: value.dataset_id):
        raise SidecarError("sidecar policy datasets are not sorted by dataset_id")
    for position, (repository_id, _dataset_id, parts) in enumerate(selector_owners):
        for other_repository_id, _other_dataset_id, other_parts in selector_owners[
            position + 1 :
        ]:
            if repository_id != other_repository_id:
                continue
            shared = min(len(parts), len(other_parts))
            if parts[:shared] == other_parts[:shared]:
                raise SidecarError(
                    "sidecar selectors overlap within one repository; every selected "
                    "path must belong to exactly one dataset"
                )
    canonical_payload = {
        "schema_version": SCHEMA_VERSION,
        "registry_id": pair.registry_id,
        "registry_generation": registry_generation,
        "policy_generation": policy_generation,
        "datasets": [
            {
                "dataset_id": dataset.dataset_id,
                "repository_id": dataset.repository_id,
                "selectors": list(dataset.selectors),
                "tier": dataset.tier,
                "adapter": dataset.adapter,
                "max_files": dataset.max_files,
                "max_total_bytes": dataset.max_total_bytes,
                "target_set_id": dataset.target_set_id,
            }
            for dataset in datasets
        ],
    }
    return PolicyDocument(
        path=path,
        registry_id=pair.registry_id,
        registry_generation=registry_generation,
        policy_generation=policy_generation,
        content_sha256=_canonical_document_hash(canonical_payload),
        datasets=tuple(datasets),
    )


def _parse_sftp_repository(path: Path) -> tuple[str, str]:
    raw = _read_secure_text_file(path, "restic repository file")
    value = raw.strip()
    if "\n" in value or "\r" in value or not value.startswith("sftp:"):
        raise SidecarError("restic repository file must contain one SFTP repository")
    if value.startswith("sftp://"):
        raise SidecarError(
            "schema v1 accepts only scp-style SFTP repositories with IPv4 or DNS hosts"
        )
    location = value[len("sftp:") :]
    if "[" in location or "]" in location:
        raise SidecarError(
            "schema v1 accepts only scp-style SFTP repositories with IPv4 or DNS hosts"
        )
    if ":" not in location:
        raise SidecarError("restic SFTP repository is missing an absolute path")
    authority, repository_path = location.split(":", 1)
    if not authority or not repository_path.startswith("/"):
        raise SidecarError(
            "restic SFTP repository must contain a host and absolute path"
        )
    if "@" not in authority:
        raise SidecarError("restic SFTP repository requires an explicit SSH user")
    user, host = authority.rsplit("@", 1)
    if not user or re.fullmatch(r"[A-Za-z0-9._-]+", user) is None:
        raise SidecarError("restic SFTP repository contains an unsafe user")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in repository_path
    ):
        raise SidecarError("restic SFTP repository contains an unsafe path")
    if not host or host.startswith("-") or any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in host
    ):
        raise SidecarError("restic SFTP repository contains an unsafe host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host != host.casefold() or len(host) > 253:
            raise SidecarError("restic SFTP repository host is not canonical DNS")
        labels = host.split(".")
        if any(
            not label
            or len(label) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
            for label in labels
        ):
            raise SidecarError("restic SFTP repository contains an unsafe DNS host")
        return host, user
    canonical = str(address)
    if host.casefold() != canonical:
        raise SidecarError("restic SFTP repository IP host is not canonical")
    return canonical, user


def _validate_password_file(path: Path) -> None:
    raw = _read_secure_text_file(path, "restic password file")
    value = raw.rstrip("\r\n")
    if not value or "\n" in value or "\r" in value:
        raise SidecarError("restic password file must contain exactly one password line")


def load_targets(path: Path, pair: visibility.RegistryPair) -> TargetsDocument:
    payload = _read_private_json(path, "sidecar targets")
    if type(payload) is not dict:
        raise SidecarError("sidecar targets root must be a JSON object")
    _require_exact_keys(payload, TARGET_ROOT_KEYS, "sidecar targets")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SidecarError(f"sidecar targets must use schema_version {SCHEMA_VERSION}")
    if payload["registry_id"] != pair.registry_id:
        raise SidecarError("sidecar targets registry_id does not match the registry")
    registry_generation = _validate_generation(
        payload["registry_generation"], "sidecar targets registry_generation"
    )
    if registry_generation != pair.generation:
        raise SidecarError("sidecar targets registry generation is stale")
    target_generation = _validate_generation(
        payload["target_generation"], "sidecar target generation"
    )
    raw_sets = payload["target_sets"]
    if type(raw_sets) is not list or len(raw_sets) > MAX_CONFIG_ITEMS:
        raise SidecarError("sidecar target_sets must be a bounded JSON array")
    target_sets: list[TargetSet] = []
    seen_set_ids: set[str] = set()
    seen_target_ids: set[str] = set()
    seen_secret_files: set[Path] = set()
    for set_index, raw_set in enumerate(raw_sets):
        label = f"sidecar target set {set_index}"
        if type(raw_set) is not dict:
            raise SidecarError(f"{label} must be a JSON object")
        _require_exact_keys(raw_set, TARGET_SET_KEYS, label)
        target_set_id = _validate_id(raw_set["target_set_id"], f"{label} id")
        if target_set_id in seen_set_ids:
            raise SidecarError("sidecar targets contain a duplicate target_set_id")
        seen_set_ids.add(target_set_id)
        tier = raw_set["tier"]
        if tier not in TIERS:
            raise SidecarError(f"{label} has an unsupported tier")
        raw_targets = raw_set["targets"]
        if (
            type(raw_targets) is not list
            or not raw_targets
            or len(raw_targets) > MAX_CONFIG_ITEMS
        ):
            raise SidecarError(f"{label} targets must be a non-empty bounded array")
        required_acks = _validate_limit(raw_set["required_acks"], f"{label} required_acks")
        if required_acks > len(raw_targets):
            raise SidecarError(f"{label} required_acks exceeds its target count")
        targets: list[Target] = []
        mesh_addresses: set[str] = set()
        failure_domains: set[str] = set()
        for target_index, raw_target in enumerate(raw_targets):
            target_label = f"{label} target {target_index}"
            if type(raw_target) is not dict:
                raise SidecarError(f"{target_label} must be a JSON object")
            _require_exact_keys(raw_target, TARGET_KEYS, target_label)
            target_id = _validate_id(raw_target["target_id"], f"{target_label} id")
            if target_id in seen_target_ids:
                raise SidecarError("sidecar targets contain a duplicate target_id")
            seen_target_ids.add(target_id)
            repository_file = _validate_absolute_secret_path(
                raw_target["repository_file"], f"{target_label} repository_file"
            )
            password_file = _validate_absolute_secret_path(
                raw_target["password_file"], f"{target_label} password_file"
            )
            identity_file = _validate_absolute_secret_path(
                raw_target["identity_file"], f"{target_label} identity_file"
            )
            if stat.S_IMODE(identity_file.lstat().st_mode) != 0o600:
                raise SidecarError("SSH identity file must use exact mode 0600")
            if len({repository_file, password_file, identity_file}) != 3:
                raise SidecarError(
                    "restic repository, password, and SSH identity files must differ"
                )
            target_secret_files = (repository_file, password_file, identity_file)
            if any(path in seen_secret_files for path in target_secret_files):
                raise SidecarError(
                    "repository, password, and identity files must be globally "
                    "unique across targets and roles"
                )
            seen_secret_files.update(target_secret_files)
            sftp_host, sftp_user = _parse_sftp_repository(repository_file)
            _validate_password_file(password_file)
            repository_sha256 = hashlib.sha256(
                _read_secure_text_file(
                    repository_file, "restic repository file"
                ).encode("utf-8")
            ).hexdigest()
            password_sha256 = hashlib.sha256(
                _read_secure_text_file(
                    password_file, "restic password file"
                ).encode("utf-8")
            ).hexdigest()
            identity_sha256 = hashlib.sha256(
                _read_secure_text_file(
                    identity_file, "SSH identity file"
                ).encode("utf-8")
            ).hexdigest()
            failure_domain = _validate_id(
                raw_target["failure_domain"], f"{target_label} failure_domain"
            )
            raw_mesh_address = raw_target["mesh_address"]
            mesh_address: str | None
            if tier == "hosted-encrypted":
                if raw_mesh_address is not None:
                    raise SidecarError("hosted targets must set mesh_address to null")
                mesh_address = None
            else:
                if type(raw_mesh_address) is not str:
                    raise SidecarError("mesh targets require a literal mesh_address")
                try:
                    address = ipaddress.ip_address(raw_mesh_address)
                    observed = ipaddress.ip_address(sftp_host)
                    mesh_address = str(address)
                    observed_address = str(observed)
                except ValueError as exc:
                    raise SidecarError(
                        "mesh target and SFTP repository hosts must be literal IP addresses"
                    ) from exc
                if mesh_address != observed_address:
                    raise SidecarError(
                        "mesh target SFTP host does not match its mesh_address"
                    )
                private_mesh_networks = (
                    ipaddress.ip_network("10.0.0.0/8"),
                    ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"),
                )
                if (
                    address.version != 4
                    or address.is_unspecified
                    or address.is_loopback
                    or address.is_link_local
                    or address.is_multicast
                    or address.is_reserved
                    or not any(address in network for network in private_mesh_networks)
                ):
                    raise SidecarError(
                        "mesh_address must be an RFC1918 IPv4 unicast address"
                    )
                if mesh_address in mesh_addresses:
                    raise SidecarError("mesh targets require distinct mesh addresses")
                if failure_domain in failure_domains:
                    raise SidecarError("mesh targets require distinct failure domains")
                mesh_addresses.add(mesh_address)
                failure_domains.add(failure_domain)
            targets.append(
                Target(
                    target_id=target_id,
                    repository_file=repository_file,
                    password_file=password_file,
                    identity_file=identity_file,
                    sftp_host=sftp_host,
                    sftp_user=sftp_user,
                    mesh_address=mesh_address,
                    failure_domain=failure_domain,
                    repository_sha256=repository_sha256,
                    password_sha256=password_sha256,
                    identity_sha256=identity_sha256,
                )
            )
        if targets != sorted(targets, key=lambda value: value.target_id):
            raise SidecarError(f"{label} targets are not sorted by target_id")
        if tier == "mesh-only":
            if len(targets) < 3:
                raise SidecarError("mesh target sets require at least three replicas")
            if required_acks <= len(targets) // 2:
                raise SidecarError("mesh target required_acks must be a strict majority")
        elif len(targets) != 1 or required_acks != 1:
            raise SidecarError(
                "hosted-encrypted target sets require exactly one target and one "
                "required acknowledgement"
            )
        target_sets.append(
            TargetSet(
                target_set_id=target_set_id,
                tier=tier,
                required_acks=required_acks,
                targets=tuple(targets),
            )
        )
    if target_sets != sorted(target_sets, key=lambda value: value.target_set_id):
        raise SidecarError("sidecar target sets are not sorted by target_set_id")
    canonical_payload = {
        "schema_version": SCHEMA_VERSION,
        "registry_id": pair.registry_id,
        "registry_generation": registry_generation,
        "target_generation": target_generation,
        "target_sets": [
            {
                "target_set_id": target_set.target_set_id,
                "tier": target_set.tier,
                "required_acks": target_set.required_acks,
                "targets": [
                    {
                        "target_id": target.target_id,
                        "repository_file": str(target.repository_file),
                        "password_file": str(target.password_file),
                        "identity_file": str(target.identity_file),
                        "mesh_address": target.mesh_address,
                        "failure_domain": target.failure_domain,
                        "repository_sha256": target.repository_sha256,
                        "password_sha256": target.password_sha256,
                        "identity_sha256": target.identity_sha256,
                    }
                    for target in target_set.targets
                ],
            }
            for target_set in target_sets
        ],
    }
    return TargetsDocument(
        path=path,
        registry_id=pair.registry_id,
        registry_generation=registry_generation,
        target_generation=target_generation,
        content_sha256=_canonical_document_hash(canonical_payload),
        target_sets=tuple(target_sets),
    )


def _target_set_lookup(targets: TargetsDocument) -> dict[str, TargetSet]:
    return {target_set.target_set_id: target_set for target_set in targets.target_sets}


def validate_policy_targets(policy: PolicyDocument, targets: TargetsDocument) -> None:
    lookup = _target_set_lookup(targets)
    for dataset in policy.datasets:
        target_set = lookup.get(dataset.target_set_id)
        if target_set is None:
            raise SidecarError("sidecar dataset references an unknown target set")
        if target_set.tier != dataset.tier:
            raise SidecarError(
                "sidecar dataset tier does not match its target set; hosted target "
                "sets cannot be used by mesh datasets"
            )


def _validate_sha256(value: object, label: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SidecarError(f"{label} must be a lowercase SHA-256 digest")
    return value


def load_state(
    path: Path,
    pair: visibility.RegistryPair,
    policy: PolicyDocument,
    targets: TargetsDocument,
) -> StateDocument:
    payload = _read_private_json(path, "sidecar state")
    if type(payload) is not dict:
        raise SidecarError("sidecar state root must be a JSON object")
    _require_exact_keys(payload, STATE_ROOT_KEYS, "sidecar state")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SidecarError(f"sidecar state must use schema_version {SCHEMA_VERSION}")
    if payload["registry_id"] != pair.registry_id:
        raise SidecarError("sidecar state registry_id does not match the registry")
    registry_generation = _validate_generation(
        payload["registry_generation"], "sidecar state registry_generation"
    )
    if registry_generation != pair.generation:
        raise SidecarError("sidecar state registry generation is stale")
    policy_generation = _validate_generation(
        payload["policy_generation"], "sidecar state policy_generation"
    )
    target_generation = _validate_generation(
        payload["target_generation"], "sidecar state target_generation"
    )
    if policy_generation != policy.policy_generation:
        raise SidecarError("sidecar state policy generation is stale")
    if target_generation != targets.target_generation:
        raise SidecarError("sidecar state target generation is stale")
    policy_sha256 = _validate_sha256(
        payload["policy_sha256"], "sidecar state policy_sha256"
    )
    target_sha256 = _validate_sha256(
        payload["target_sha256"], "sidecar state target_sha256"
    )
    if policy_sha256 != policy.content_sha256:
        raise SidecarError("sidecar state is bound to different policy content")
    if target_sha256 != targets.content_sha256:
        raise SidecarError(
            "sidecar state is bound to different target or credential content"
        )
    state_generation = _validate_generation(
        payload["state_generation"], "sidecar state generation"
    )
    if payload["coordinator_mode"] != COORDINATOR_MODE:
        raise SidecarError(
            "sidecar state must declare the standalone no-failover coordinator mode"
        )
    raw_datasets = payload["datasets"]
    if type(raw_datasets) is not list or len(raw_datasets) > MAX_CONFIG_ITEMS:
        raise SidecarError("sidecar state datasets must be a bounded JSON array")
    policy_lookup = {dataset.dataset_id: dataset for dataset in policy.datasets}
    target_lookup = _target_set_lookup(targets)
    datasets: list[DatasetState] = []
    seen: set[str] = set()
    for index, raw_dataset in enumerate(raw_datasets):
        label = f"sidecar state dataset {index}"
        if type(raw_dataset) is not dict:
            raise SidecarError(f"{label} must be a JSON object")
        _require_exact_keys(raw_dataset, STATE_DATASET_KEYS, label)
        dataset_id = _validate_id(raw_dataset["dataset_id"], f"{label} dataset_id")
        if dataset_id in seen:
            raise SidecarError("sidecar state contains a duplicate dataset_id")
        seen.add(dataset_id)
        policy_dataset = policy_lookup.get(dataset_id)
        if policy_dataset is None:
            raise SidecarError("sidecar state contains an unknown dataset_id")
        if raw_dataset["repository_id"] != policy_dataset.repository_id:
            raise SidecarError("sidecar state repository identity drifted")
        sequence = _validate_generation(raw_dataset["sequence"], f"{label} sequence")
        file_count = _validate_generation(
            raw_dataset["file_count"], f"{label} file_count"
        )
        total_bytes = _validate_generation(
            raw_dataset["total_bytes"], f"{label} total_bytes"
        )
        raw_replicas = raw_dataset["replicas"]
        if type(raw_replicas) is not list or len(raw_replicas) > MAX_CONFIG_ITEMS:
            raise SidecarError(f"{label} replicas must be a bounded JSON array")
        replicas: list[ReplicaState] = []
        replica_ids: set[str] = set()
        allowed_target_set = target_lookup[policy_dataset.target_set_id]
        allowed_targets = {
            target.target_id for target in allowed_target_set.targets
        }
        for replica_index, raw_replica in enumerate(raw_replicas):
            replica_label = f"{label} replica {replica_index}"
            if type(raw_replica) is not dict:
                raise SidecarError(f"{replica_label} must be a JSON object")
            _require_exact_keys(raw_replica, REPLICA_KEYS, replica_label)
            target_id = _validate_id(
                raw_replica["target_id"], f"{replica_label} target_id"
            )
            if target_id in replica_ids or target_id not in allowed_targets:
                raise SidecarError(f"{label} contains an invalid replica target")
            replica_ids.add(target_id)
            snapshot_id = raw_replica["snapshot_id"]
            if type(snapshot_id) is not str or RESTIC_SNAPSHOT_RE.fullmatch(snapshot_id) is None:
                raise SidecarError(f"{replica_label} has an invalid restic snapshot ID")
            replicas.append(ReplicaState(target_id, snapshot_id))
        if replicas != sorted(replicas, key=lambda value: value.target_id):
            raise SidecarError(f"{label} replicas are not sorted by target_id")
        manifest_value = raw_dataset["manifest_sha256"]
        committed_at = raw_dataset["committed_at"]
        if sequence == 0:
            if (
                manifest_value is not None
                or committed_at is not None
                or file_count != 0
                or total_bytes != 0
                or replicas
            ):
                raise SidecarError("empty sidecar state contains committed data")
            manifest_sha256 = None
        else:
            manifest_sha256 = _validate_sha256(
                manifest_value, f"{label} manifest_sha256"
            )
            if (
                type(committed_at) is not str
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", committed_at)
                is None
            ):
                raise SidecarError(f"{label} committed_at is not a UTC timestamp")
            if file_count <= 0 or len(replicas) < allowed_target_set.required_acks:
                raise SidecarError("committed sidecar state lacks required acknowledgements")
        datasets.append(
            DatasetState(
                dataset_id=dataset_id,
                repository_id=policy_dataset.repository_id,
                sequence=sequence,
                manifest_sha256=manifest_sha256,
                file_count=file_count,
                total_bytes=total_bytes,
                committed_at=committed_at,
                replicas=tuple(replicas),
            )
        )
    if datasets != sorted(datasets, key=lambda value: value.dataset_id):
        raise SidecarError("sidecar state datasets are not sorted by dataset_id")
    if set(policy_lookup) != seen:
        raise SidecarError("sidecar state does not exactly cover the policy datasets")
    return StateDocument(
        path=path,
        registry_id=pair.registry_id,
        registry_generation=registry_generation,
        policy_generation=policy_generation,
        policy_sha256=policy_sha256,
        target_generation=target_generation,
        target_sha256=target_sha256,
        state_generation=state_generation,
        coordinator_mode=COORDINATOR_MODE,
        datasets=tuple(datasets),
    )


def _state_payload(
    pair: visibility.RegistryPair,
    policy: PolicyDocument,
    targets: TargetsDocument,
    datasets: Sequence[DatasetState],
    *,
    state_generation: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_id": pair.registry_id,
        "registry_generation": pair.generation,
        "policy_generation": policy.policy_generation,
        "policy_sha256": policy.content_sha256,
        "target_generation": targets.target_generation,
        "target_sha256": targets.content_sha256,
        "state_generation": state_generation,
        "coordinator_mode": COORDINATOR_MODE,
        "datasets": [
            {
                "dataset_id": dataset.dataset_id,
                "repository_id": dataset.repository_id,
                "sequence": dataset.sequence,
                "manifest_sha256": dataset.manifest_sha256,
                "file_count": dataset.file_count,
                "total_bytes": dataset.total_bytes,
                "committed_at": dataset.committed_at,
                "replicas": [
                    {
                        "target_id": replica.target_id,
                        "snapshot_id": replica.snapshot_id,
                    }
                    for replica in dataset.replicas
                ],
            }
            for dataset in datasets
        ],
    }


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise SidecarError("sidecar state parent must not be a symlink")
        metadata = path.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise SidecarError("sidecar state parent must be a directory")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise SidecarError("sidecar state parent is not owned by the current user")
        os.chmod(path, 0o700)
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise SidecarError("sidecar state parent is not owner-only mode 0700")
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError("cannot secure sidecar state parent") from exc


class _SidecarLock:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.directory: Path | None = None
        self.directory_descriptor: int | None = None
        self.descriptor: int | None = None

    def __enter__(self) -> "_SidecarLock":
        _ensure_private_directory(self.state_path.parent)
        try:
            self.directory = self.state_path.parent.resolve(strict=True)
            observed = self.directory.lstat()
            self.directory_descriptor = os.open(
                self.directory,
                _directory_open_flags(),
            )
            opened_directory = os.fstat(self.directory_descriptor)
            if (
                not stat.S_ISDIR(opened_directory.st_mode)
                or not _same_file_identity(opened_directory, observed)
                or (hasattr(os, "getuid") and opened_directory.st_uid != os.getuid())
                or stat.S_IMODE(opened_directory.st_mode) != 0o700
            ):
                raise SidecarError("sidecar control directory is not owner-only")
            flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            self.descriptor = os.open(
                ".portfolio-sidecar.lock",
                flags,
                0o600,
                dir_fd=self.directory_descriptor,
            )
            metadata = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise SidecarError("sidecar lock file is unsafe")
            os.fchmod(self.descriptor, 0o600)
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
            current = self.directory.lstat()
            if not _same_file_identity(current, opened_directory):
                raise SidecarError("sidecar control directory changed while locking")
        except SidecarError:
            self.__exit__(None, None, None)
            raise
        except OSError as exc:
            self.__exit__(None, None, None)
            raise SidecarError("cannot acquire the sidecar control lock") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.descriptor is not None:
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self.descriptor)
                self.descriptor = None
        if self.directory_descriptor is not None:
            os.close(self.directory_descriptor)
            self.directory_descriptor = None


def _thaw_staging_descriptor(descriptor: int) -> None:
    os.fchmod(descriptor, 0o700)
    with os.scandir(descriptor) as iterator:
        names = tuple(entry.name for entry in iterator)
    for name in names:
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child = os.open(name, _directory_open_flags(), dir_fd=descriptor)
            try:
                _thaw_staging_descriptor(child)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child = os.open(name, _file_open_flags(), dir_fd=descriptor)
            try:
                os.fchmod(child, 0o600)
            finally:
                os.close(child)
        else:
            raise SidecarError("sidecar staging cleanup found an unsafe node")


class _StagingArea:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.spool_root: Path | None = None
        self.run_root: Path | None = None
        self.run_identity: tuple[int, int] | None = None

    def __enter__(self) -> Path:
        control_directory = self.state_path.parent.resolve(strict=True)
        self.spool_root = control_directory / "spool"
        if any(character.isspace() for character in str(self.spool_root)):
            raise SidecarError("sidecar spool path may not contain whitespace")
        try:
            visibility._require_ignored_or_outside_git(self.spool_root)
        except visibility.RegistryError as exc:
            raise SidecarError(
                "sidecar spool does not satisfy the private ignored-path contract"
            ) from exc
        _ensure_private_directory(self.spool_root)
        try:
            spool_metadata = self.spool_root.lstat()
            if (
                not stat.S_ISDIR(spool_metadata.st_mode)
                or stat.S_ISLNK(spool_metadata.st_mode)
                or (hasattr(os, "getuid") and spool_metadata.st_uid != os.getuid())
                or stat.S_IMODE(spool_metadata.st_mode) != 0o700
            ):
                raise SidecarError("sidecar spool is not an owner-only real directory")
            with os.scandir(self.spool_root) as iterator:
                if next(iterator, None) is not None:
                    raise SidecarError(
                        "stale sidecar staging data exists; inspect and remove it "
                        "before retrying"
                    )
            self.run_root = Path(
                tempfile.mkdtemp(prefix="run-", dir=self.spool_root)
            ).resolve(strict=True)
            run_metadata = self.run_root.lstat()
            self.run_identity = (run_metadata.st_dev, run_metadata.st_ino)
            if (
                self.run_root.parent != self.spool_root
                or not self.run_root.name.startswith("run-")
                or not stat.S_ISDIR(run_metadata.st_mode)
                or stat.S_ISLNK(run_metadata.st_mode)
                or (hasattr(os, "getuid") and run_metadata.st_uid != os.getuid())
            ):
                raise SidecarError("new sidecar staging root is unsafe")
            self.run_root.chmod(0o700)
            for child_name in ("datasets", "credentials"):
                child = self.run_root / child_name
                child.mkdir(mode=0o700)
                child.chmod(0o700)
            return self.run_root
        except BaseException:
            self._cleanup(suppress_errors=True)
            raise

    def _cleanup(self, *, suppress_errors: bool) -> None:
        if self.run_root is None:
            return
        try:
            metadata = self.run_root.lstat()
            if (
                self.spool_root is None
                or self.run_root.parent != self.spool_root
                or not self.run_root.name.startswith("run-")
                or self.run_identity != (metadata.st_dev, metadata.st_ino)
                or not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
            ):
                raise SidecarError("refusing to clean an unverified staging root")
            descriptor = os.open(self.run_root, _directory_open_flags())
            try:
                _thaw_staging_descriptor(descriptor)
            finally:
                os.close(descriptor)
            shutil.rmtree(self.run_root)
        except (OSError, SidecarError) as exc:
            if not suppress_errors:
                raise SidecarError(
                    "could not safely remove the sidecar staging root"
                ) from exc
        finally:
            self.run_root = None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._cleanup(suppress_errors=exc_type is not None)


def _write_state_json(path: Path, payload: dict[str, Any], *, replace: bool) -> None:
    _ensure_private_directory(path.parent)
    _require_ignored_private_path(path, "sidecar state", exists=replace)
    if not replace and (path.exists() or path.is_symlink()):
        raise SidecarError("init-state refuses to overwrite an existing state file")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp.local.json",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        _require_ignored_private_path(
            temporary, "sidecar state temporary file", exists=True
        )
        os.fchmod(descriptor, 0o600)
        serialized = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        offset = 0
        while offset < len(serialized):
            written = os.write(descriptor, serialized[offset:])
            if written <= 0:
                raise OSError("short sidecar state write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if not replace and (path.exists() or path.is_symlink()):
            raise SidecarError("sidecar state appeared during initialization")
        os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError("cannot atomically write sidecar state") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_new_private_json(
    path: Path,
    payload: dict[str, Any],
    *,
    label: str,
    directory_descriptor: int,
) -> _CreatedPrivateFile:
    """Publish one new ignored JSON document without overwriting any path."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp.local.json",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    temporary_metadata = os.fstat(descriptor)
    temporary_file: _CreatedPrivateFile | None = _CreatedPrivateFile(
        path=temporary,
        device=temporary_metadata.st_dev,
        inode=temporary_metadata.st_ino,
    )
    published_file: _CreatedPrivateFile | None = None
    complete = False
    try:
        _require_stable_private_bootstrap_path(
            temporary,
            f"{label} temporary file",
            exists=True,
        )
        os.fchmod(descriptor, 0o600)
        serialized = (
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        offset = 0
        while offset < len(serialized):
            written = os.write(descriptor, serialized[offset:])
            if written <= 0:
                raise OSError("short sidecar config write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1

        try:
            os.link(
                temporary.name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise SidecarError(
                "init-config refuses to overwrite an existing policy or targets file"
            ) from exc
        published_file = _CreatedPrivateFile(
            path=path,
            device=temporary_metadata.st_dev,
            inode=temporary_metadata.st_ino,
        )
        published_metadata = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (published_metadata.st_dev, published_metadata.st_ino) != (
            published_file.device,
            published_file.inode,
        ):
            raise SidecarError(
                "init-config destination changed during atomic publication"
            )
        _unlink_created_private_file(temporary_file, directory_descriptor)
        temporary_file = None
        os.fsync(directory_descriptor)
        _require_stable_private_bootstrap_path(path, label, exists=True)
        complete = True
        return published_file
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError(f"cannot atomically create {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        cleanup_error: BaseException | None = None
        for created_file in (
            temporary_file,
            published_file if not complete else None,
        ):
            if created_file is None:
                continue
            try:
                _unlink_created_private_file(created_file, directory_descriptor)
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise SidecarError(
                "init-config cleanup refused to remove a changed file"
            ) from cleanup_error


def _unlink_created_private_file(
    created_file: _CreatedPrivateFile,
    directory_descriptor: int,
) -> None:
    try:
        current = os.stat(
            created_file.path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) != (
        created_file.device,
        created_file.inode,
    ):
        raise SidecarError(
            "init-config rollback found a changed file and left it untouched"
        )
    os.unlink(created_file.path.name, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)


def _remove_initialized_config(
    files: Sequence[_CreatedPrivateFile],
    directory_descriptor: int,
) -> None:
    for created_file in reversed(files):
        _unlink_created_private_file(created_file, directory_descriptor)


def _git_environment() -> dict[str, str]:
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
    hardened = (
        ("core.attributesFile", os.devnull),
        ("core.excludesFile", os.devnull),
        ("core.fsmonitor", "false"),
        ("core.hooksPath", os.devnull),
        ("core.pager", "cat"),
        ("core.untrackedCache", "false"),
        ("fetch.recurseSubmodules", "false"),
        ("fetch.writeCommitGraph", "false"),
        ("maintenance.auto", "false"),
        ("submodule.recurse", "false"),
    )
    for index, (key, value) in enumerate(hardened):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": str(len(hardened)),
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
    return environment


def _run_git(
    checkout: Path,
    arguments: Sequence[str],
    *,
    input_data: bytes | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(checkout),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=text,
            input=input_data,
            timeout=120.0,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SidecarError("cannot run hardened Git inspection") from exc


def _parse_local_configuration(raw: bytes) -> tuple[tuple[str, str], ...]:
    configuration: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            raw_key, raw_value = record.split(b"\n", 1)
            configuration.append(
                (raw_key.decode("utf-8"), raw_value.decode("utf-8"))
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise SidecarError("repository-local Git configuration is malformed") from exc
    return tuple(configuration)


def _verify_checkout(
    checkout: Path,
    expected_entry: visibility.RepositoryEntry,
) -> None:
    if checkout.is_symlink() or not checkout.is_dir():
        raise SidecarError("sidecar repository checkout is unavailable")
    git_directory = checkout / ".git"
    try:
        git_metadata = git_directory.lstat()
    except OSError as exc:
        raise SidecarError("sidecar checkout has no real Git control directory") from exc
    if not stat.S_ISDIR(git_metadata.st_mode) or stat.S_ISLNK(git_metadata.st_mode):
        raise SidecarError("sidecar checkout must be a standalone Git worktree")
    configuration_result = _run_git(
        checkout,
        ["config", "--local", "--no-includes", "--null", "--list"],
        text=False,
    )
    if configuration_result.returncode != 0:
        raise SidecarError("cannot read repository-local Git configuration")
    configuration = _parse_local_configuration(configuration_result.stdout)
    lowered_keys = {key.casefold() for key, _ in configuration}
    if "include.path" in lowered_keys or any(
        key.startswith("includeif.") and key.endswith(".path") for key in lowered_keys
    ):
        raise SidecarError("repository-local Git configuration includes are not allowed")
    if "core.worktree" in lowered_keys:
        raise SidecarError("repository-local core.worktree redirection is not allowed")
    top_level = _run_git(checkout, ["rev-parse", "--show-toplevel"])
    absolute_git = _run_git(checkout, ["rev-parse", "--absolute-git-dir"])
    if (
        top_level.returncode != 0
        or absolute_git.returncode != 0
        or Path(top_level.stdout.strip()).resolve() != checkout.resolve()
        or Path(absolute_git.stdout.strip()).resolve() != git_directory.resolve()
    ):
        raise SidecarError("sidecar checkout identity cannot be established")
    remote_names: set[str] = set()
    remote_urls: list[str] = []
    for key, value in configuration:
        match = re.fullmatch(r"remote\.(.+)\.(?:url|pushurl)", key)
        if match is None:
            continue
        remote_names.add(match.group(1))
        remote_urls.append(value)
    if "origin" not in remote_names or not remote_urls:
        raise SidecarError("sidecar checkout has no usable origin remote")
    expected_slug = expected_entry.slug.casefold()
    for remote_url in remote_urls:
        observed = visibility._normalize_github_remote(remote_url)
        if observed is None or observed.casefold() != expected_slug:
            raise SidecarError("sidecar checkout remote identity does not match registry")
    clean_result = _run_git(
        checkout,
        ["diff", "--quiet", "--no-ext-diff", "HEAD", "--"],
    )
    if clean_result.returncode != 0:
        raise SidecarError("sidecar checkout has tracked changes relative to HEAD")
    unmerged_result = _run_git(checkout, ["ls-files", "--unmerged", "-z"], text=False)
    if unmerged_result.returncode != 0 or unmerged_result.stdout:
        raise SidecarError("sidecar checkout has unmerged index entries")
    flags_result = _run_git(checkout, ["ls-files", "-v", "-z"], text=False)
    if flags_result.returncode != 0:
        raise SidecarError("cannot inspect sidecar checkout index flags")
    for record in flags_result.stdout.split(b"\0"):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b" ":
            raise SidecarError("Git returned malformed index flag evidence")
        flag = chr(record[0])
        _decode_git_path(record[2:], "index filename")
        if flag == "S" or flag.islower():
            raise SidecarError(
                "sidecar checkout uses skip-worktree or assume-unchanged index flags"
            )


def _decode_git_path(raw: bytes, label: str) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SidecarError(f"{label} is not valid UTF-8") from exc
    if (
        not value
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise SidecarError(f"{label} is not a safe normalized path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part.casefold() == ".git" for part in pure.parts)
    ):
        raise SidecarError(f"{label} is not a safe relative path")
    return value


def _prove_untracked_and_ignored(checkout: Path, paths: Sequence[str]) -> None:
    indexed_result = _run_git(
        checkout, ["ls-files", "--cached", "-z", "--"], text=False
    )
    head_result = _run_git(
        checkout, ["ls-tree", "-r", "--name-only", "-z", "HEAD", "--"], text=False
    )
    if indexed_result.returncode != 0 or head_result.returncode != 0:
        raise SidecarError("cannot enumerate the Git index")
    indexed_paths = {
        _decode_git_path(raw_path, "tracked filename")
        for raw_path in indexed_result.stdout.split(b"\0")
        if raw_path
    }
    head_paths = {
        _decode_git_path(raw_path, "HEAD filename")
        for raw_path in head_result.stdout.split(b"\0")
        if raw_path
    }
    tracked_paths = indexed_paths | head_paths
    tracked_path_keys = {path.casefold() for path in tracked_paths}
    if any(path.casefold() in tracked_path_keys for path in paths):
        raise SidecarError("selected sidecar data contains a Git-tracked file")
    raw_input = b"".join(path.encode("utf-8") + b"\0" for path in paths)
    ignored_result = _run_git(
        checkout,
        ["check-ignore", "--no-index", "-z", "-v", "--stdin"],
        input_data=raw_input,
        text=False,
    )
    fields = ignored_result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 4:
        raise SidecarError("Git returned malformed ignore-rule evidence")
    matched_paths: set[str] = set()
    rule_sources: set[str] = set()
    for offset in range(0, len(fields), 4):
        raw_source, _raw_line, raw_pattern, raw_path = fields[offset : offset + 4]
        source = _decode_git_path(raw_source, "ignore rule source")
        matched = _decode_git_path(raw_path, "ignored filename")
        try:
            pattern = raw_pattern.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SidecarError("tracked ignore rule is not valid UTF-8") from exc
        if PurePosixPath(source).name != ".gitignore" or source not in tracked_paths:
            raise SidecarError(
                "selected sidecar data is not ignored by a tracked .gitignore rule"
            )
        if pattern.startswith("!"):
            raise SidecarError("selected sidecar data matched a negated ignore rule")
        matched_paths.add(matched)
        rule_sources.add(source)
    if ignored_result.returncode != 0 or matched_paths != set(paths):
        raise SidecarError(
            "every selected sidecar file must be untracked and ignored by tracked "
            ".gitignore rules"
        )
    for source in sorted(rule_sources):
        flag_result = _run_git(
            checkout,
            ["ls-files", "-v", "-z", "--", source],
            text=False,
        )
        flag_records = [
            record for record in flag_result.stdout.split(b"\0") if record
        ]
        if (
            flag_result.returncode != 0
            or len(flag_records) != 1
            or len(flag_records[0]) < 3
            or flag_records[0][1:2] != b" "
        ):
            raise SidecarError("cannot inspect tracked .gitignore index flags")
        flag = chr(flag_records[0][0])
        if flag == "S" or flag.islower():
            raise SidecarError(
                "tracked .gitignore uses skip-worktree or assume-unchanged"
            )
        stable_rule = _run_git(
            checkout,
            [
                "diff",
                "--quiet",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
                source,
            ],
        )
        if stable_rule.returncode != 0:
            raise SidecarError("tracked .gitignore rule changed relative to HEAD")


def _require_stable_ignored_or_outside_git(path: Path) -> None:
    """Require a tracked, unchanged ignore rule when a path is inside Git."""

    try:
        absolute_path = Path(os.path.realpath(os.path.abspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise SidecarError("sidecar bootstrap path is invalid") from exc
    probe = absolute_path.parent
    try:
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
    except (OSError, ValueError) as exc:
        raise SidecarError("sidecar bootstrap path cannot be inspected") from exc
    if not probe.is_dir():
        raise SidecarError("sidecar bootstrap parent is not a directory")
    worktree_result = _run_git(probe, ["rev-parse", "--show-toplevel"])
    if worktree_result.returncode != 0:
        ancestor = probe
        while True:
            marker = ancestor / ".git"
            try:
                marker.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise SidecarError(
                    "cannot inspect sidecar bootstrap Git containment"
                ) from exc
            else:
                raise SidecarError(
                    "cannot validate the sidecar bootstrap Git worktree"
                )
            if ancestor == ancestor.parent:
                break
            ancestor = ancestor.parent
        return
    try:
        worktree = Path(os.path.realpath(worktree_result.stdout.strip()))
        relative_path = absolute_path.relative_to(worktree).as_posix()
    except (OSError, TypeError, ValueError) as exc:
        raise SidecarError(
            "cannot establish sidecar bootstrap containment in Git"
        ) from exc
    try:
        _prove_untracked_and_ignored(worktree, (relative_path,))
    except SidecarError as exc:
        raise SidecarError(
            "sidecar bootstrap paths inside Git require a tracked, unchanged "
            ".gitignore rule"
        ) from exc


def _require_stable_private_bootstrap_path(
    path: Path,
    label: str,
    *,
    exists: bool,
) -> None:
    _require_ignored_private_path(path, label, exists=exists)
    _require_stable_ignored_or_outside_git(path)


def _metadata_tuple(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_open_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise SidecarError("sidecar capture requires no-follow directory support")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_open_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise SidecarError("sidecar capture requires no-follow file support")
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _open_checkout_descriptor(checkout: Path, observed: os.stat_result) -> int:
    descriptor = -1
    try:
        descriptor = os.open(checkout, _directory_open_flags())
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_file_identity(opened, observed):
            raise SidecarError("sidecar checkout changed before secure capture")
        return descriptor
    except SidecarError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SidecarError("cannot open sidecar checkout without following links") from exc


def _open_directory_beneath(
    root_descriptor: int,
    parts: Sequence[str],
    root_device: int,
) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            child = os.open(
                part,
                _directory_open_flags(),
                dir_fd=descriptor,
            )
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_dev != root_device:
                os.close(child)
                raise SidecarError(
                    "sidecar file traversal left its checkout filesystem"
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except SidecarError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise SidecarError(
            "sidecar file traversal encountered a link or changed directory"
        ) from exc


def _open_staging_parent(root_descriptor: int, parts: Sequence[str]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, _directory_open_flags(), dir_fd=descriptor)
            metadata = os.fstat(child)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                os.close(child)
                raise SidecarError("sidecar staging directory is not owner-only")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except SidecarError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise SidecarError("cannot create private sidecar staging directory") from exc


def _copy_descriptor(
    source: int,
    destination: int | None,
    digest: Any,
    *,
    expected_bytes: int,
    collector: bytearray | None = None,
) -> None:
    remaining = expected_bytes
    while remaining:
        chunk = os.read(source, min(1024 * 1024, remaining))
        if not chunk:
            raise SidecarError("sidecar input became shorter during capture")
        digest.update(chunk)
        if collector is not None:
            collector.extend(chunk)
        if destination is not None:
            offset = 0
            while offset < len(chunk):
                written = os.write(destination, chunk[offset:])
                if written <= 0:
                    raise OSError("short sidecar staging write")
                offset += written
        remaining -= len(chunk)
    if os.read(source, 1):
        raise SidecarError("sidecar input grew during capture")


def _read_stable_private_text(path: Path, label: str) -> str:
    parent_descriptor = -1
    source_descriptor = -1
    try:
        parent_observed = path.parent.lstat()
        parent_descriptor = os.open(path.parent, _directory_open_flags())
        if not _same_file_identity(os.fstat(parent_descriptor), parent_observed):
            raise SidecarError(f"{label} parent changed during secure read")
        observed = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        source_descriptor = os.open(
            path.name,
            _file_open_flags(),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_SECRET_BYTES
            or not _same_file_identity(opened, observed)
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise SidecarError(f"{label} is unavailable or insecure")
        raw = bytearray()
        _copy_descriptor(
            source_descriptor,
            None,
            hashlib.sha256(),
            expected_bytes=opened.st_size,
            collector=raw,
        )
        if _metadata_tuple(os.fstat(source_descriptor)) != _metadata_tuple(opened):
            raise SidecarError(f"{label} changed during secure read")
        return raw.decode("utf-8")
    except SidecarError:
        raise
    except UnicodeDecodeError as exc:
        raise SidecarError(f"{label} is not valid UTF-8") from exc
    except OSError as exc:
        raise SidecarError(f"{label} is unavailable or insecure") from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _capture_regular_file(
    checkout_descriptor: int,
    relative_path: str,
    root_device: int,
    *,
    remaining_bytes: int,
    staging_descriptor: int | None,
) -> FileSnapshot:
    parts = PurePosixPath(relative_path).parts
    source_parent = _open_directory_beneath(
        checkout_descriptor,
        parts[:-1],
        root_device,
    )
    source = -1
    destination_parent = -1
    destination = -1
    destination_created = False
    try:
        before = os.stat(parts[-1], dir_fd=source_parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise SidecarError("sidecar selection contains a non-regular file")
        if before.st_nlink != 1:
            raise SidecarError("sidecar selection contains a hard-linked file")
        if before.st_dev != root_device:
            raise SidecarError("sidecar selection crosses a filesystem mount")
        if before.st_size > remaining_bytes:
            raise SidecarError("sidecar dataset exceeds max_total_bytes")
        source = os.open(parts[-1], _file_open_flags(), dir_fd=source_parent)
        opened = os.fstat(source)
        if _metadata_tuple(opened) != _metadata_tuple(before):
            raise SidecarError("selected sidecar file changed before capture")
        digest = hashlib.sha256()
        if staging_descriptor is None:
            _copy_descriptor(
                source,
                None,
                digest,
                expected_bytes=before.st_size,
            )
        else:
            destination_parent = _open_staging_parent(
                staging_descriptor,
                parts[:-1],
            )
            destination_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            destination = os.open(
                parts[-1],
                destination_flags,
                0o600,
                dir_fd=destination_parent,
            )
            destination_created = True
            _copy_descriptor(
                source,
                destination,
                digest,
                expected_bytes=before.st_size,
            )
            os.fsync(destination)
            os.fchmod(destination, 0o400)
            staged = os.fstat(destination)
            if not stat.S_ISREG(staged.st_mode) or staged.st_size != before.st_size:
                raise SidecarError("sidecar staging copy is incomplete")
        after = os.fstat(source)
        if _metadata_tuple(after) != _metadata_tuple(before):
            raise SidecarError("selected sidecar file changed during capture")
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError("cannot read selected sidecar file safely") from exc
    finally:
        if destination >= 0:
            os.close(destination)
        if destination_created and sys.exc_info()[0] is not None:
            try:
                os.unlink(parts[-1], dir_fd=destination_parent)
            except OSError:
                pass
        if destination_parent >= 0:
            os.close(destination_parent)
        if source >= 0:
            os.close(source)
        os.close(source_parent)
    return FileSnapshot(
        relative_path=relative_path,
        size=before.st_size,
        mode=stat.S_IMODE(before.st_mode),
        uid=before.st_uid,
        gid=before.st_gid,
        device=before.st_dev,
        inode=before.st_ino,
        mtime_ns=before.st_mtime_ns,
        ctime_ns=before.st_ctime_ns,
        sha256=digest.hexdigest(),
    )


def _safe_selector_path(checkout: Path, selector: str, root_device: int) -> Path:
    current = checkout
    for part in PurePosixPath(selector).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SidecarError("sidecar selector does not exist") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SidecarError("sidecar selector traverses a symlink")
        if metadata.st_dev != root_device:
            raise SidecarError("sidecar selector crosses a filesystem mount")
    return current


def _validate_discovered_relative(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        raise SidecarError("selected sidecar filename is not normalized UTF-8 text")
    pure = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in pure.parts) or any(
        part.casefold() == ".git" for part in pure.parts
    ):
        raise SidecarError("selected sidecar tree contains nested Git control data")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SidecarError(
            "selected sidecar filename is not normalized UTF-8 text"
        ) from exc
    return value


def _enumerate_selector(
    checkout: Path,
    selector: str,
    root_device: int,
    *,
    node_budget: list[int],
) -> list[str]:
    target = _safe_selector_path(checkout, selector, root_device)
    results: list[str] = []

    node_budget[0] -= 1
    if node_budget[0] < 0:
        raise SidecarError("sidecar selection exceeds its traversal safety limit")

    def visit(path: Path, relative_path: str) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SidecarError("sidecar selection changed during enumeration") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SidecarError("sidecar selection contains a symlink")
        if metadata.st_dev != root_device:
            raise SidecarError("sidecar selection crosses a filesystem mount")
        if stat.S_ISREG(metadata.st_mode):
            _validate_discovered_relative(relative_path)
            results.append(relative_path)
            return
        if not stat.S_ISDIR(metadata.st_mode):
            raise SidecarError("sidecar selection contains a special file")
        if PurePosixPath(relative_path).name.casefold() == ".git":
            raise SidecarError("sidecar selection contains nested Git control data")
        children: list[os.DirEntry[str]] = []
        try:
            with os.scandir(path) as iterator:
                for child in iterator:
                    if len(children) >= node_budget[0]:
                        raise SidecarError(
                            "sidecar selection exceeds its traversal safety limit"
                        )
                    children.append(child)
        except OSError as exc:
            raise SidecarError("cannot enumerate selected sidecar directory") from exc
        node_budget[0] -= len(children)
        children.sort(key=lambda entry: entry.name.casefold())
        for child in children:
            child_relative = f"{relative_path}/{child.name}"
            _validate_discovered_relative(child_relative)
            visit(Path(child.path), child_relative)

    visit(target, selector)
    return results


def _open_staging_descriptor(path: Path) -> int:
    descriptor = -1
    try:
        observed = path.lstat()
        descriptor = os.open(path, _directory_open_flags())
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_file_identity(opened, observed)
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            raise SidecarError("sidecar staging root is not an owner-only real directory")
        return descriptor
    except SidecarError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SidecarError("cannot open private sidecar staging root") from exc


def _freeze_staging_descriptor(descriptor: int) -> None:
    try:
        with os.scandir(descriptor) as iterator:
            names = sorted((entry.name for entry in iterator), key=str.casefold)
        for name in names:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                child = os.open(name, _directory_open_flags(), dir_fd=descriptor)
                try:
                    _freeze_staging_descriptor(child)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                child = os.open(name, _file_open_flags(), dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if (
                        opened.st_nlink != 1
                        or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
                    ):
                        raise SidecarError("sidecar staging file identity is unsafe")
                    os.fchmod(child, 0o400)
                finally:
                    os.close(child)
            else:
                raise SidecarError("sidecar staging tree contains an unsafe node")
        os.fchmod(descriptor, 0o500)
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError("cannot freeze private sidecar staging tree") from exc


def capture_dataset(
    dataset: DatasetPolicy,
    checkout: Path,
    expected_entry: visibility.RepositoryEntry,
    *,
    staging_root: Path | None = None,
) -> DatasetCapture:
    _verify_checkout(checkout, expected_entry)
    try:
        root_metadata = checkout.lstat()
    except OSError as exc:
        raise SidecarError("sidecar checkout became unavailable") from exc
    checkout_descriptor = _open_checkout_descriptor(checkout, root_metadata)
    staging_descriptor = (
        _open_staging_descriptor(staging_root) if staging_root is not None else None
    )
    try:
        node_budget = [min(dataset.max_files, MAX_DATASET_FILES) + MAX_CONFIG_ITEMS]
        paths: list[str] = []
        for selector in dataset.selectors:
            paths.extend(
                _enumerate_selector(
                    checkout,
                    selector,
                    root_metadata.st_dev,
                    node_budget=node_budget,
                )
            )
        if not paths:
            raise SidecarError("sidecar dataset contains no regular files")
        if len(paths) != len(set(paths)):
            raise SidecarError("sidecar dataset enumeration contains overlapping files")
        paths.sort(key=lambda value: value.casefold())
        if len(paths) > dataset.max_files:
            raise SidecarError("sidecar dataset exceeds max_files")
        _prove_untracked_and_ignored(checkout, paths)
        files: list[FileSnapshot] = []
        total_bytes = 0
        for relative_path in paths:
            captured = _capture_regular_file(
                checkout_descriptor,
                relative_path,
                root_metadata.st_dev,
                remaining_bytes=dataset.max_total_bytes - total_bytes,
                staging_descriptor=staging_descriptor,
            )
            total_bytes += captured.size
            files.append(captured)
        try:
            current_root = checkout.lstat()
        except OSError as exc:
            raise SidecarError("sidecar checkout changed after secure capture") from exc
        if not _same_file_identity(current_root, os.fstat(checkout_descriptor)):
            raise SidecarError("sidecar checkout identity changed during secure capture")
        _verify_checkout(checkout, expected_entry)
        _prove_untracked_and_ignored(checkout, paths)
        if staging_descriptor is not None:
            _freeze_staging_descriptor(staging_descriptor)
    finally:
        if staging_descriptor is not None:
            os.close(staging_descriptor)
        os.close(checkout_descriptor)
    manifest = [
        {
            "path": file.relative_path,
            "size": file.size,
            "mode": file.mode,
            "uid": file.uid,
            "gid": file.gid,
            "device": file.device,
            "inode": file.inode,
            "mtime_ns": file.mtime_ns,
            "ctime_ns": file.ctime_ns,
            "sha256": file.sha256,
        }
        for file in files
    ]
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DatasetCapture(
        dataset_id=dataset.dataset_id,
        checkout=checkout,
        files=tuple(files),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        total_bytes=total_bytes,
        backup_root=staging_root,
    )


def _stable_input_hash(
    source: Path,
    *,
    forbidden_mode: int,
    label: str,
) -> str:
    parent_descriptor = -1
    source_descriptor = -1
    try:
        parent_observed = source.parent.lstat()
        parent_descriptor = os.open(source.parent, _directory_open_flags())
        if not _same_file_identity(os.fstat(parent_descriptor), parent_observed):
            raise SidecarError(f"{label} parent changed during validation")
        observed = os.stat(source.name, dir_fd=parent_descriptor, follow_symlinks=False)
        source_descriptor = os.open(
            source.name,
            _file_open_flags(),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_SECRET_BYTES
            or not _same_file_identity(opened, observed)
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
            or stat.S_IMODE(opened.st_mode) & forbidden_mode
        ):
            raise SidecarError(f"{label} is not a safe stable input")
        digest = hashlib.sha256()
        _copy_descriptor(
            source_descriptor,
            None,
            digest,
            expected_bytes=opened.st_size,
        )
        if _metadata_tuple(os.fstat(source_descriptor)) != _metadata_tuple(opened):
            raise SidecarError(f"{label} changed during validation")
        return digest.hexdigest()
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError(f"cannot validate {label} safely") from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _copy_input_to_staging(
    source: Path,
    destination: Path,
    expected_hash: str,
    *,
    forbidden_mode: int,
) -> None:
    parent_descriptor = -1
    source_descriptor = -1
    destination_descriptor = -1
    try:
        parent_observed = source.parent.lstat()
        parent_descriptor = os.open(source.parent, _directory_open_flags())
        if not _same_file_identity(os.fstat(parent_descriptor), parent_observed):
            raise SidecarError("secret parent changed before staging")
        observed = os.stat(source.name, dir_fd=parent_descriptor, follow_symlinks=False)
        source_descriptor = os.open(
            source.name,
            _file_open_flags(),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_SECRET_BYTES
            or not _same_file_identity(opened, observed)
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
            or stat.S_IMODE(opened.st_mode) & forbidden_mode
        ):
            raise SidecarError("secret file is unsafe for staging")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        digest = hashlib.sha256()
        _copy_descriptor(
            source_descriptor,
            destination_descriptor,
            digest,
            expected_bytes=opened.st_size,
        )
        os.fsync(destination_descriptor)
        os.fchmod(destination_descriptor, 0o400)
        if digest.hexdigest() != expected_hash:
            raise SidecarError("secret content changed before immutable staging")
        if _metadata_tuple(os.fstat(source_descriptor)) != _metadata_tuple(opened):
            raise SidecarError("secret content changed during immutable staging")
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError("cannot create immutable staged secret") from exc
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _copy_secret_to_staging(source: Path, destination: Path, expected_hash: str) -> None:
    _copy_input_to_staging(
        source,
        destination,
        expected_hash,
        forbidden_mode=0o077,
    )


def _stage_targets(
    targets: TargetsDocument,
    staging_root: Path,
) -> dict[str, StagedTarget]:
    credentials_root = staging_root / "credentials"
    staged: dict[str, StagedTarget] = {}
    for target_set in targets.target_sets:
        for target in target_set.targets:
            target_root = credentials_root / target.target_id
            try:
                target_root.mkdir(mode=0o700, exist_ok=False)
                target_root.chmod(0o700)
            except OSError as exc:
                raise SidecarError("cannot create target credential staging root") from exc
            repository_file = target_root / "repository"
            password_file = target_root / "password"
            identity_file = target_root / "identity"
            _copy_secret_to_staging(
                target.repository_file,
                repository_file,
                target.repository_sha256,
            )
            _copy_secret_to_staging(
                target.password_file,
                password_file,
                target.password_sha256,
            )
            _copy_secret_to_staging(
                target.identity_file,
                identity_file,
                target.identity_sha256,
            )
            target_descriptor = _open_staging_descriptor(target_root)
            try:
                _freeze_staging_descriptor(target_descriptor)
            finally:
                os.close(target_descriptor)
            staged[target.target_id] = StagedTarget(
                repository_file=repository_file,
                password_file=password_file,
                identity_file=identity_file,
            )
    return staged


def _stage_known_hosts(
    known_hosts: KnownHostsFile,
    staging_root: Path,
) -> Path:
    destination = staging_root / "credentials" / "known_hosts"
    _copy_input_to_staging(
        known_hosts.path,
        destination,
        known_hosts.sha256,
        forbidden_mode=0o022,
    )
    return destination


def _resolve_executable(value: str, label: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        located = shutil.which(value)
        if located is None:
            raise SidecarError(f"cannot resolve required {label} executable")
        resolved = Path(located).resolve()
    try:
        metadata = resolved.lstat()
    except OSError as exc:
        raise SidecarError(f"cannot inspect resolved {label} executable") from exc
    if (
        not resolved.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or not os.access(resolved, os.X_OK)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SidecarError(f"resolved {label} is not a safe executable file")
    return resolved


def _validate_known_hosts(path: Path) -> KnownHostsFile:
    if not path.is_absolute():
        raise SidecarError("known_hosts must be an absolute path")
    try:
        path = path.parent.resolve(strict=True) / path.name
    except OSError as exc:
        raise SidecarError("known_hosts parent directory is unavailable") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SidecarError("known_hosts file is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SidecarError("known_hosts must be a real non-writable regular file")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise SidecarError("known_hosts is not owned by the current user")
    if any(character.isspace() for character in str(path)):
        raise SidecarError("known_hosts path may not contain whitespace")
    return KnownHostsFile(
        path=path,
        sha256=_stable_input_hash(
            path,
            forbidden_mode=0o022,
            label="known_hosts",
        ),
    )


def _restic_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
    }


def _restic_command_token(value: str) -> str:
    if RESTIC_COMMAND_TOKEN_RE.fullmatch(value) is not None:
        return value
    if (
        not value
        or "'" in value
        or "\\" in value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise SidecarError("sidecar SSH command contains an unsafe token")
    return f"'{value}'"


def _restic_sftp_command(
    ssh: Path,
    known_hosts: Path,
    target: Target,
    staged_target: StagedTarget,
) -> str:
    tokens = [
        str(ssh),
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        f"IdentityFile={staged_target.identity_file}",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RemoteCommand=none",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "RequestTTY=no",
    ]
    tokens.extend(("-l", target.sftp_user))
    tokens.extend((target.sftp_host, "-s", "sftp"))
    return " ".join(_restic_command_token(token) for token in tokens)


def _parse_restic_snapshot_id(raw_output: bytes) -> str:
    if len(raw_output) > MAX_RESTIC_OUTPUT_BYTES:
        raise SidecarError("restic output exceeded the safety limit")
    snapshot_ids: list[str] = []
    try:
        text = raw_output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SidecarError("restic returned non-UTF-8 status output") from exc
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line, object_pairs_hook=_object_without_duplicate_keys)
        except (json.JSONDecodeError, SidecarError) as exc:
            raise SidecarError("restic returned malformed JSON status output") from exc
        if type(value) is not dict:
            continue
        snapshot_id = value.get("snapshot_id")
        if snapshot_id is None:
            continue
        if type(snapshot_id) is not str or RESTIC_SNAPSHOT_RE.fullmatch(snapshot_id) is None:
            raise SidecarError("restic returned an invalid snapshot identifier")
        snapshot_ids.append(snapshot_id)
    if not snapshot_ids or len(set(snapshot_ids)) != 1:
        raise SidecarError("restic did not return one unambiguous snapshot identifier")
    return snapshot_ids[0]


def _set_linux_child_subreaper(enabled: bool) -> bool | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.restype = ctypes.c_int
        current = ctypes.c_int()
        zero = ctypes.c_ulong(0)
        if (
            prctl(
                ctypes.c_int(_PR_GET_CHILD_SUBREAPER),
                ctypes.byref(current),
                zero,
                zero,
                zero,
            )
            != 0
        ):
            raise OSError(ctypes.get_errno(), "prctl get failed")
        previous = bool(current.value)
        if previous != enabled and (
            prctl(
                ctypes.c_int(_PR_SET_CHILD_SUBREAPER),
                ctypes.c_ulong(int(enabled)),
                zero,
                zero,
                zero,
            )
            != 0
        ):
            raise OSError(ctypes.get_errno(), "prctl set failed")
        return previous
    except (AttributeError, OSError) as exc:
        raise SidecarError(
            "Linux child-subreaper process isolation is unavailable"
        ) from exc


def _linux_group_has_owned_live_child(process_group: int) -> bool:
    while True:
        try:
            child, _status = os.waitpid(-process_group, os.WNOHANG)
        except InterruptedError:
            continue
        except ChildProcessError:
            return False
        if child == 0:
            return True


def _reap_linux_process_group(process_group: int) -> None:
    deadline = time.monotonic() + 5.0
    while True:
        if not _linux_group_has_owned_live_child(process_group):
            return
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            raise SidecarError("restic process group could not be reaped") from exc
        if time.monotonic() >= deadline:
            raise SidecarError("restic process group could not be reaped")
        time.sleep(0.01)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if getattr(process, "_sidecar_ownership_lost", False):
        raise SidecarError(
            "restic process ownership was lost; its process group was not signaled"
        )
    leader_is_anchored = process.returncode is None
    if not leader_is_anchored:
        if not sys.platform.startswith("linux"):
            return
        if not _linux_group_has_owned_live_child(process.pid):
            return
    permission_error: PermissionError | None = None
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        if not getattr(process, "_sidecar_exit_observed", False):
            try:
                exited = _process_exited_without_reaping(process)
            except SidecarError as observation_error:
                raise SidecarError(
                    "restic process group could not be terminated safely"
                ) from observation_error
            if not exited:
                raise SidecarError(
                    "restic process group could not be terminated"
                ) from exc
        permission_error = exc
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    if permission_error is not None:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        raise SidecarError("restic process group could not be terminated") from permission_error
    if sys.platform.startswith("linux"):
        _reap_linux_process_group(process.pid)


def _run_bounded_process(
    command: Sequence[str],
    *,
    cwd: Path,
    input_data: bytes,
    environment: dict[str, str],
    timeout: float,
) -> tuple[int, bytes, bytes]:
    with _PROCESS_RUNNER_LOCK:
        previous_subreaper = _set_linux_child_subreaper(True)
        try:
            return _run_bounded_process_locked(
                command,
                cwd=cwd,
                input_data=input_data,
                environment=environment,
                timeout=timeout,
            )
        finally:
            if previous_subreaper is not None:
                _set_linux_child_subreaper(previous_subreaper)


def _run_bounded_process_locked(
    command: Sequence[str],
    *,
    cwd: Path,
    input_data: bytes,
    environment: dict[str, str],
    timeout: float,
) -> tuple[int, bytes, bytes]:
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise SidecarError(
            "restic process isolation requires default SIGCHLD handling"
        )
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        raise SidecarError("restic backup process could not start") from exc
    try:
        result = _communicate_bounded_process(
            process,
            input_data=input_data,
            timeout=timeout,
        )
    except BaseException as primary_error:
        try:
            _kill_process_group(process)
        except BaseException as cleanup_error:
            raise cleanup_error from primary_error
        raise
    _kill_process_group(process)
    if process.returncode is None:
        raise SidecarError("restic process status was not collected safely")
    stdout, stderr = result
    return process.returncode, stdout, stderr


def _darwin_process_exited_without_reaping(process: subprocess.Popen[bytes]) -> bool:
    global _DARWIN_WAITID
    try:
        if _DARWIN_WAITID is None:
            libc = ctypes.CDLL(None, use_errno=True)
            waitid = libc.waitid
            waitid.argtypes = (
                ctypes.c_int,
                ctypes.c_uint,
                ctypes.POINTER(_DarwinSiginfo),
                ctypes.c_int,
            )
            waitid.restype = ctypes.c_int
            _DARWIN_WAITID = waitid
        info = _DarwinSiginfo()
        while True:
            ctypes.set_errno(0)
            result = _DARWIN_WAITID(
                ctypes.c_int(os.P_PID),
                ctypes.c_uint(process.pid),
                ctypes.byref(info),
                ctypes.c_int(os.WEXITED | os.WNOHANG | os.WNOWAIT),
            )
            if result == 0:
                exited = info.pid != 0
                if exited:
                    setattr(process, "_sidecar_exit_observed", True)
                return exited
            error_number = ctypes.get_errno()
            if error_number == errno.EINTR:
                continue
            if error_number == errno.ECHILD:
                setattr(process, "_sidecar_ownership_lost", True)
                raise SidecarError("restic process ownership was lost")
            raise OSError(error_number, "libc waitid failed")
    except SidecarError:
        raise
    except (AttributeError, OSError) as exc:
        raise SidecarError(
            "Darwin non-reaping process observation is unavailable"
        ) from exc


def _process_exited_without_reaping(process: subprocess.Popen[bytes]) -> bool:
    required = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    if any(not hasattr(os, name) for name in required):
        raise SidecarError("sidecar process isolation requires non-reaping wait support")
    if not hasattr(os, "waitid"):
        if sys.platform == "darwin":
            return _darwin_process_exited_without_reaping(process)
        raise SidecarError("sidecar process isolation requires non-reaping wait support")
    while True:
        try:
            result = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except InterruptedError:
            continue
        except ChildProcessError as exc:
            setattr(process, "_sidecar_ownership_lost", True)
            raise SidecarError("restic process ownership was lost") from exc
        except OSError as exc:
            if exc.errno == errno.ECHILD:
                setattr(process, "_sidecar_ownership_lost", True)
                raise SidecarError("restic process ownership was lost") from exc
            raise
        exited = result is not None
        if exited:
            setattr(process, "_sidecar_exit_observed", True)
        return exited


def _communicate_bounded_process(
    process: subprocess.Popen[bytes],
    *,
    input_data: bytes,
    timeout: float,
) -> tuple[bytes, bytes]:
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    input_offset = 0

    def close_stream(stream: Any) -> None:
        if stream is None:
            return
        if selector is not None:
            try:
                selector.unregister(stream)
            except (KeyError, ValueError):
                pass
        try:
            stream.close()
        except OSError:
            pass

    try:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise SidecarError("restic backup process pipes are unavailable")
        selector = selectors.DefaultSelector()
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        if input_data:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SidecarError("restic backup process timed out")
            if not selector.get_map():
                if _process_exited_without_reaping(process):
                    break
                time.sleep(min(0.01, remaining))
                continue
            events = selector.select(timeout=min(0.25, remaining))
            for key, _mask in events:
                stream = key.fileobj
                if key.data == "stdin":
                    try:
                        written = os.write(
                            stream.fileno(),
                            input_data[input_offset : input_offset + 64 * 1024],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        close_stream(stream)
                        continue
                    if written <= 0:
                        close_stream(stream)
                        continue
                    input_offset += written
                    if input_offset == len(input_data):
                        close_stream(stream)
                    continue

                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    close_stream(stream)
                    continue
                destination = stdout if key.data == "stdout" else stderr
                if len(destination) + len(chunk) > MAX_RESTIC_OUTPUT_BYTES:
                    raise SidecarError("restic output exceeded the safety limit")
                destination.extend(chunk)

        return bytes(stdout), bytes(stderr)
    except SidecarError:
        raise
    except OSError as exc:
        raise SidecarError("restic backup process could not complete") from exc
    finally:
        close_stream(process.stdin)
        close_stream(process.stdout)
        close_stream(process.stderr)
        if selector is not None:
            try:
                selector.close()
            except OSError:
                pass


def _run_restic_backup(
    restic: Path,
    ssh: Path,
    known_hosts: Path,
    target: Target,
    staged_target: StagedTarget,
    capture: DatasetCapture,
) -> str:
    ssh_command = _restic_sftp_command(
        ssh,
        known_hosts,
        target,
        staged_target,
    )
    command = [
        str(restic),
        "--no-cache",
        "--repository-file",
        str(staged_target.repository_file),
        "--password-file",
        str(staged_target.password_file),
        "-o",
        f"sftp.command={ssh_command}",
        "backup",
        "--json",
        "--no-scan",
        "--files-from-raw",
        "-",
    ]
    file_list = b"".join(
        file.relative_path.encode("utf-8") + b"\0" for file in capture.files
    )
    if capture.backup_root is None:
        raise SidecarError("restic backup requires a private staging tree")
    returncode, raw_stdout, _raw_stderr = _run_bounded_process(
        command,
        cwd=capture.backup_root,
        input_data=file_list,
        environment=_restic_environment(),
        timeout=3600.0,
    )
    if returncode != 0:
        raise SidecarError("restic target backup failed")
    return _parse_restic_snapshot_id(raw_stdout)


def _catalog_checkout(
    pair: visibility.RegistryPair,
    catalog: materializer.CatalogDocument,
    portfolio_root: Path,
    dataset: DatasetPolicy,
) -> tuple[Path, visibility.RepositoryEntry]:
    catalog_entry = next(
        (
            entry
            for entry in catalog.repositories
            if entry.repository_id == dataset.repository_id
        ),
        None,
    )
    if catalog_entry is None or catalog_entry.desired_presence != "checkout":
        raise SidecarError("sidecar dataset repository is not configured as a checkout")
    registry_entry = next(
        (
            entry
            for _, entry in pair.entries
            if entry.repository_id == dataset.repository_id
        ),
        None,
    )
    if registry_entry is None:
        raise SidecarError("sidecar dataset repository identity disappeared")
    try:
        checkout = materializer._target_path(
            portfolio_root,
            catalog_entry.relative_path,
        )
    except materializer.MaterializerError as exc:
        raise SidecarError("sidecar checkout path is unsafe") from exc
    return checkout, registry_entry


def _reload_policy_targets(
    pair: visibility.RegistryPair,
    policy: PolicyDocument,
    targets: TargetsDocument,
) -> None:
    current_policy = load_policy(policy.path, pair)
    current_targets = load_targets(targets.path, pair)
    validate_policy_targets(current_policy, current_targets)
    if current_policy != policy or current_targets != targets:
        raise SidecarError("sidecar policy or target content changed during operation")


@dataclass(frozen=True)
class _Inputs:
    pair: visibility.RegistryPair
    catalog: materializer.CatalogDocument
    portfolio_root: Path
    policy: PolicyDocument
    targets: TargetsDocument
    state_path: Path
    state: StateDocument | None


def _control_path(value: str) -> Path:
    if type(value) is not str or not value or "\0" in value:
        raise SidecarError("sidecar control path is invalid")
    try:
        return Path(os.path.abspath(value))
    except (OSError, TypeError, ValueError) as exc:
        raise SidecarError("sidecar control path is invalid") from exc


def _load_inputs(arguments: argparse.Namespace, *, load_existing_state: bool) -> _Inputs:
    try:
        pair = visibility.load_pair(arguments.private, arguments.public)
        portfolio_root = materializer._safe_root(arguments.portfolio_root)
        catalog = materializer.load_catalog(arguments.catalog, pair)
    except (visibility.RegistryError, materializer.MaterializerError) as exc:
        raise SidecarError(
            "sidecar registry, catalog, or portfolio-root validation failed"
        ) from exc
    policy = load_policy(_control_path(arguments.policy), pair)
    targets = load_targets(_control_path(arguments.targets), pair)
    validate_policy_targets(policy, targets)
    state_path = _control_path(arguments.state)
    state = (
        load_state(state_path, pair, policy, targets)
        if load_existing_state
        else None
    )
    return _Inputs(
        pair=pair,
        catalog=catalog,
        portfolio_root=portfolio_root,
        policy=policy,
        targets=targets,
        state_path=state_path,
        state=state,
    )


def _locked_current_inputs(inputs: _Inputs) -> tuple[
    visibility.RegistryPair,
    materializer.CatalogDocument,
]:
    try:
        current_pair = materializer._reload_registry_snapshot(inputs.pair)
        current_catalog = materializer._reload_catalog_snapshot(
            current_pair,
            inputs.catalog,
        )
        _reload_policy_targets(current_pair, inputs.policy, inputs.targets)
        if inputs.state is not None:
            current_state = load_state(
                inputs.state_path,
                current_pair,
                inputs.policy,
                inputs.targets,
            )
            if current_state != inputs.state:
                raise SidecarError("sidecar state changed during operation")
        return current_pair, current_catalog
    except (visibility.RegistryError, materializer.MaterializerError) as exc:
        raise SidecarError("sidecar governance snapshot could not be revalidated") from exc


def initialize_config(
    private_path_value: str,
    public_path_value: str,
    policy_path_value: str,
    targets_path_value: str,
) -> tuple[PolicyDocument, TargetsDocument]:
    """Create an inert, registry-bound local policy/targets pair."""

    policy_path = _control_path(policy_path_value)
    targets_path = _control_path(targets_path_value)
    if policy_path == targets_path:
        raise SidecarError("sidecar policy and targets paths must differ")
    if policy_path.parent != targets_path.parent:
        raise SidecarError(
            "sidecar policy and targets must share one owner-only control directory"
        )
    try:
        pair = visibility.load_pair(private_path_value, public_path_value)
    except (OSError, TypeError, ValueError, visibility.RegistryError) as exc:
        raise SidecarError("sidecar visibility-registry validation failed") from exc

    policy_payload = {
        "schema_version": SCHEMA_VERSION,
        "registry_id": pair.registry_id,
        "registry_generation": pair.generation,
        "policy_generation": 0,
        "datasets": [],
    }
    targets_payload = {
        "schema_version": SCHEMA_VERSION,
        "registry_id": pair.registry_id,
        "registry_generation": pair.generation,
        "target_generation": 0,
        "target_sets": [],
    }

    for path in (
        policy_path,
        targets_path,
        policy_path.parent / ".portfolio-sidecar.lock",
    ):
        _require_stable_ignored_or_outside_git(path)
    _ensure_private_directory(policy_path.parent)
    try:
        control_directory = policy_path.parent.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise SidecarError("sidecar bootstrap control directory is unavailable") from exc
    policy_path = control_directory / policy_path.name
    targets_path = control_directory / targets_path.name
    for path in (
        policy_path,
        targets_path,
        policy_path.parent / ".portfolio-sidecar.lock",
    ):
        _require_stable_private_bootstrap_path(
            path,
            "sidecar bootstrap destination",
            exists=False,
        )

    created: list[_CreatedPrivateFile] = []
    with _SidecarLock(policy_path) as control_lock:
        if control_lock.directory_descriptor is None:
            raise SidecarError("sidecar bootstrap control lock is unavailable")
        directory_descriptor = control_lock.directory_descriptor
        for path in (policy_path, targets_path):
            if path.exists() or path.is_symlink():
                raise SidecarError(
                    "init-config refuses to overwrite an existing policy or "
                    "targets file"
                )
            _require_stable_private_bootstrap_path(
                path,
                "sidecar bootstrap destination",
                exists=False,
            )
        try:
            with materializer._locked_registry_snapshot(pair) as current_pair:
                created.append(
                    _write_new_private_json(
                        policy_path,
                        policy_payload,
                        label="sidecar policy",
                        directory_descriptor=directory_descriptor,
                    )
                )
                created.append(
                    _write_new_private_json(
                        targets_path,
                        targets_payload,
                        label="sidecar targets",
                        directory_descriptor=directory_descriptor,
                    )
                )
                policy = load_policy(policy_path, current_pair)
                targets = load_targets(targets_path, current_pair)
                validate_policy_targets(policy, targets)
        except BaseException as exc:
            try:
                _remove_initialized_config(created, directory_descriptor)
            except (OSError, SidecarError) as cleanup_error:
                raise SidecarError(
                    "init-config failed and could not roll back its new files"
                ) from cleanup_error
            if isinstance(exc, materializer.MaterializerError):
                raise SidecarError(
                    "sidecar visibility registry changed during init-config"
                ) from exc
            if isinstance(exc, (OSError, TypeError, ValueError)):
                raise SidecarError("cannot initialize sidecar config") from exc
            raise

    return policy, targets


def initialize_state(inputs: _Inputs) -> StateDocument:
    _ensure_private_directory(inputs.state_path.parent)
    with _SidecarLock(inputs.state_path):
        if inputs.state_path.exists() or inputs.state_path.is_symlink():
            raise SidecarError("init-state refuses to overwrite an existing state file")
        try:
            with materializer._locked_registry_snapshot(inputs.pair) as current_pair:
                with materializer._CatalogLock(inputs.catalog.path):
                    current_catalog = materializer._reload_catalog_snapshot(
                        current_pair,
                        inputs.catalog,
                    )
                    if current_catalog != inputs.catalog:
                        raise SidecarError("portfolio catalog changed during init-state")
                    _reload_policy_targets(current_pair, inputs.policy, inputs.targets)
                    datasets = tuple(
                        DatasetState(
                            dataset_id=dataset.dataset_id,
                            repository_id=dataset.repository_id,
                            sequence=0,
                            manifest_sha256=None,
                            file_count=0,
                            total_bytes=0,
                            committed_at=None,
                            replicas=(),
                        )
                        for dataset in inputs.policy.datasets
                    )
                    _write_state_json(
                        inputs.state_path,
                        _state_payload(
                            current_pair,
                            inputs.policy,
                            inputs.targets,
                            datasets,
                            state_generation=0,
                        ),
                        replace=False,
                    )
                    return load_state(
                        inputs.state_path,
                        current_pair,
                        inputs.policy,
                        inputs.targets,
                    )
        except (visibility.RegistryError, materializer.MaterializerError) as exc:
            raise SidecarError(
                "sidecar governance snapshot could not be locked for init-state"
            ) from exc


def _capture_all(
    pair: visibility.RegistryPair,
    catalog: materializer.CatalogDocument,
    portfolio_root: Path,
    policy: PolicyDocument,
    *,
    staging_root: Path | None = None,
) -> tuple[DatasetCapture, ...]:
    captures: list[DatasetCapture] = []
    for dataset in policy.datasets:
        checkout, expected_entry = _catalog_checkout(
            pair,
            catalog,
            portfolio_root,
            dataset,
        )
        dataset_staging_root: Path | None = None
        if staging_root is not None:
            dataset_staging_root = staging_root / "datasets" / dataset.dataset_id
            try:
                dataset_staging_root.mkdir(mode=0o700, exist_ok=False)
                dataset_staging_root.chmod(0o700)
            except OSError as exc:
                raise SidecarError("cannot create dataset staging root") from exc
        captures.append(
            capture_dataset(
                dataset,
                checkout,
                expected_entry,
                staging_root=dataset_staging_root,
            )
        )
    return tuple(captures)


def plan(inputs: _Inputs, *, show_paths: bool) -> tuple[str, ...]:
    assert inputs.state is not None
    with _SidecarLock(inputs.state_path):
        try:
            with materializer._locked_registry_snapshot(inputs.pair) as current_pair:
                with materializer._CatalogLock(inputs.catalog.path):
                    current_pair, current_catalog = _locked_current_inputs(inputs)
                    captures = _capture_all(
                        current_pair,
                        current_catalog,
                        inputs.portfolio_root,
                        inputs.policy,
                    )
                    _locked_current_inputs(inputs)
        except (visibility.RegistryError, materializer.MaterializerError) as exc:
            raise SidecarError(
                "sidecar governance snapshot could not be locked for planning"
            ) from exc
    target_lookup = _target_set_lookup(inputs.targets)
    state_lookup = {
        dataset.dataset_id: dataset for dataset in inputs.state.datasets
    }
    lines = [
        "coordinator\tstandalone; mesh nodes are storage replicas only; "
        "automatic failover requires quorum authority integration"
    ]
    for dataset, capture in zip(inputs.policy.datasets, captures, strict=True):
        target_set = target_lookup[dataset.target_set_id]
        current_state = state_lookup[dataset.dataset_id]
        lines.append(
            "\t".join(
                (
                    "backup",
                    dataset.dataset_id,
                    dataset.tier,
                    f"files={len(capture.files)}",
                    f"bytes={capture.total_bytes}",
                    f"targets={len(target_set.targets)}",
                    f"required_acks={target_set.required_acks}",
                    f"committed_sequence={current_state.sequence}",
                )
            )
        )
        if show_paths:
            lines.extend(
                f"path\t{dataset.dataset_id}\t{file.relative_path}"
                for file in capture.files
            )
    return tuple(lines)


def backup(
    inputs: _Inputs,
    *,
    restic: Path,
    ssh: Path,
    known_hosts: KnownHostsFile,
) -> tuple[tuple[str, ...], bool]:
    assert inputs.state is not None
    messages: list[str] = []
    degraded = False
    with _SidecarLock(inputs.state_path):
        with _StagingArea(inputs.state_path) as staging_root:
            try:
                with materializer._locked_registry_snapshot(inputs.pair) as current_pair:
                    with materializer._CatalogLock(inputs.catalog.path):
                        current_pair, current_catalog = _locked_current_inputs(inputs)
                        captures = _capture_all(
                            current_pair,
                            current_catalog,
                            inputs.portfolio_root,
                            inputs.policy,
                            staging_root=staging_root,
                        )
                        staged_targets = _stage_targets(inputs.targets, staging_root)
                        staged_known_hosts = _stage_known_hosts(
                            known_hosts,
                            staging_root,
                        )
                        _locked_current_inputs(inputs)
            except (visibility.RegistryError, materializer.MaterializerError) as exc:
                raise SidecarError(
                    "sidecar governance snapshot could not be locked for capture"
                ) from exc
            target_lookup = _target_set_lookup(inputs.targets)
            policy_lookup = {
                dataset.dataset_id: dataset for dataset in inputs.policy.datasets
            }
            updated_states = {
                dataset.dataset_id: dataset for dataset in inputs.state.datasets
            }
            committed_captures: dict[str, DatasetCapture] = {}
            for capture in captures:
                dataset = policy_lookup[capture.dataset_id]
                target_set = target_lookup[dataset.target_set_id]
                replicas: list[ReplicaState] = []
                for target in target_set.targets:
                    try:
                        snapshot_id = _run_restic_backup(
                            restic,
                            ssh,
                            staged_known_hosts,
                            target,
                            staged_targets[target.target_id],
                            capture,
                        )
                    except SidecarError:
                        degraded = True
                        continue
                    replicas.append(ReplicaState(target.target_id, snapshot_id))
                if len(replicas) < target_set.required_acks:
                    degraded = True
                    messages.append(
                        f"not-committed\t{dataset.dataset_id}\tacknowledgements="
                        f"{len(replicas)}/{target_set.required_acks}"
                    )
                    continue
                previous = updated_states[dataset.dataset_id]
                updated_states[dataset.dataset_id] = DatasetState(
                    dataset_id=dataset.dataset_id,
                    repository_id=dataset.repository_id,
                    sequence=previous.sequence + 1,
                    manifest_sha256=capture.manifest_sha256,
                    file_count=len(capture.files),
                    total_bytes=capture.total_bytes,
                    committed_at=datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    replicas=tuple(sorted(replicas, key=lambda value: value.target_id)),
                )
                committed_captures[dataset.dataset_id] = capture
                if len(replicas) < len(target_set.targets):
                    degraded = True
                    disposition = "committed-degraded"
                else:
                    disposition = "committed"
                messages.append(
                    f"{disposition}\t{dataset.dataset_id}\tacknowledgements="
                    f"{len(replicas)}/{len(target_set.targets)}"
                )
            try:
                with materializer._locked_registry_snapshot(inputs.pair) as commit_pair:
                    with materializer._CatalogLock(inputs.catalog.path):
                        if _validate_known_hosts(known_hosts.path) != known_hosts:
                            raise SidecarError(
                                "known_hosts content changed during backup"
                            )
                        commit_pair, commit_catalog = _locked_current_inputs(inputs)
                        for dataset_id, expected_capture in committed_captures.items():
                            dataset = policy_lookup[dataset_id]
                            checkout, expected_entry = _catalog_checkout(
                                commit_pair,
                                commit_catalog,
                                inputs.portfolio_root,
                                dataset,
                            )
                            if (
                                capture_dataset(dataset, checkout, expected_entry)
                                != expected_capture
                            ):
                                raise SidecarError(
                                    "sidecar data changed before state commit"
                                )
                        if committed_captures:
                            ordered_states = tuple(
                                updated_states[dataset.dataset_id]
                                for dataset in inputs.policy.datasets
                            )
                            _write_state_json(
                                inputs.state_path,
                                _state_payload(
                                    commit_pair,
                                    inputs.policy,
                                    inputs.targets,
                                    ordered_states,
                                    state_generation=inputs.state.state_generation + 1,
                                ),
                                replace=True,
                            )
                            load_state(
                                inputs.state_path,
                                commit_pair,
                                inputs.policy,
                                inputs.targets,
                            )
            except (visibility.RegistryError, materializer.MaterializerError) as exc:
                raise SidecarError(
                    "sidecar governance snapshot could not be locked for commit"
                ) from exc
    return tuple(messages), degraded


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--private", required=True)
    parser.add_argument("--public", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--portfolio-root", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--state", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Coordinate client-encrypted ignored-data backups. This v1 process "
            "has no automatic failover; mesh nodes are storage replicas only."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_config_parser = subparsers.add_parser(
        "init-config",
        help="Create an inert ignored policy/targets pair bound to the registry.",
    )
    init_config_parser.add_argument("--private", required=True)
    init_config_parser.add_argument("--public", required=True)
    init_config_parser.add_argument("--policy", required=True)
    init_config_parser.add_argument("--targets", required=True)
    for command in ("validate", "init-state", "plan", "backup"):
        command_parser = subparsers.add_parser(command)
        _add_common_arguments(command_parser)
        if command == "plan":
            command_parser.add_argument("--show-paths", action="store_true")
        if command == "backup":
            command_parser.add_argument("--restic", default="restic")
            command_parser.add_argument("--ssh", default="ssh")
            command_parser.add_argument(
                "--known-hosts",
                default=str(Path.home() / ".ssh" / "known_hosts"),
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "init-config":
            policy, targets = initialize_config(
                arguments.private,
                arguments.public,
                arguments.policy,
                arguments.targets,
            )
            print(
                f"initialized inert sidecar config: {len(policy.datasets)} "
                f"dataset(s), {len(targets.target_sets)} target set(s); "
                "no data is protected"
            )
            print(
                "next: explicitly populate policy and targets, provision "
                "credentials, then run init-state"
            )
            return 0
        inputs = _load_inputs(
            arguments,
            load_existing_state=arguments.command != "init-state",
        )
        if arguments.command == "init-state":
            state = initialize_state(inputs)
            print(
                f"initialized standalone sidecar state generation "
                f"{state.state_generation}: {len(state.datasets)} dataset(s)"
            )
            return 0
        assert inputs.state is not None
        if arguments.command == "validate":
            with _SidecarLock(inputs.state_path):
                try:
                    with materializer._locked_registry_snapshot(inputs.pair):
                        with materializer._CatalogLock(inputs.catalog.path):
                            _locked_current_inputs(inputs)
                except (visibility.RegistryError, materializer.MaterializerError) as exc:
                    raise SidecarError(
                        "sidecar governance snapshot could not be locked for validation"
                    ) from exc
            print(
                f"valid standalone sidecar configuration: "
                f"{len(inputs.policy.datasets)} dataset(s)"
            )
            return 0
        if arguments.command == "plan":
            for line in plan(inputs, show_paths=arguments.show_paths):
                print(line)
            return 0
        restic = _resolve_executable(arguments.restic, "restic")
        ssh = _resolve_executable(arguments.ssh, "ssh")
        known_hosts = _validate_known_hosts(_control_path(arguments.known_hosts))
        messages, degraded = backup(
            inputs,
            restic=restic,
            ssh=ssh,
            known_hosts=known_hosts,
        )
        for message in messages:
            print(message)
        if degraded:
            print(
                "error: sidecar backup was partial or degraded; committed state "
                "was retained only where required acknowledgements succeeded",
                file=sys.stderr,
            )
            return 3
        print("sidecar backup complete")
        return 0
    except KeyboardInterrupt:
        print(
            "error: sidecar operation interrupted; no success was reported",
            file=sys.stderr,
        )
        return 130
    except SidecarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
