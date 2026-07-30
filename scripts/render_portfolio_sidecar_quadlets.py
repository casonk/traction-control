#!/usr/bin/env python3
"""Render inactive rootless Podman-on-WireGuard sidecar target bundles.

The renderer is deliberately narrower than a deployment tool.  It validates an
ignored owner-only document, writes new owner-only review artifacts, and never
invokes Podman, systemd, WireGuard, or a secret provider.  SFTP target Quadlets
are renderable today; the coordinator remains a non-activatable review artifact
until its exact repository mounts, scheduling, and credential rotation contract
can be bound to the sidecar policy.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
COORDINATOR_MODE = "standalone-no-automatic-failover"
MAX_CONFIG_BYTES = 1024 * 1024
MIN_MESH_TARGETS = 3
MAX_TARGETS = 64
MIN_ROOTLESS_PORT = 1024
MAX_PORT = 65535
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
RUNTIME_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
IMAGE_RE = re.compile(
    r"(?:[a-z0-9][a-z0-9._:/-]{0,383}@sha256:[0-9a-f]{64}|"
    r"sha256:[0-9a-f]{64})"
)
RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
ROOT_KEYS = {
    "schema_version",
    "deployment_id",
    "deployment_generation",
    "coordinator_mode",
    "target_set_id",
    "coordinator",
    "targets",
}
COORDINATOR_KEYS = {"image", "unit_name", "container_name"}
TARGET_KEYS = {
    "target_id",
    "failure_domain",
    "image",
    "unit_name",
    "container_name",
    "mesh_address",
    "published_port",
    "container_port",
    "repository_volume",
    "host_key_secret",
    "authorized_keys_secret",
}
SIDECAR_TARGET_ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "registry_generation",
    "target_generation",
    "target_sets",
}
SIDECAR_TARGET_SET_KEYS = {"target_set_id", "tier", "required_acks", "targets"}
SIDECAR_TARGET_REQUIRED_KEYS = {
    "target_id",
    "repository_file",
    "password_file",
    "identity_file",
    "mesh_address",
    "failure_domain",
}
SIDECAR_TARGET_ALLOWED_KEYS = SIDECAR_TARGET_REQUIRED_KEYS | {"sftp_port"}


class MeshRenderError(Exception):
    """Raised when private deployment input or rendered output is unsafe."""


@dataclass(frozen=True)
class CoordinatorSpec:
    image: str
    unit_name: str
    container_name: str


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    failure_domain: str
    image: str
    unit_name: str
    container_name: str
    mesh_address: str
    published_port: int
    container_port: int
    repository_volume: str
    host_key_secret: str
    authorized_keys_secret: str


@dataclass(frozen=True)
class AuthoritativeTarget:
    target_id: str
    failure_domain: str
    mesh_address: str
    sftp_port: int


@dataclass(frozen=True)
class TargetTopology:
    registry_id: str
    registry_generation: int
    target_set_id: str
    target_generation: int
    required_acks: int
    targets: tuple[AuthoritativeTarget, ...]
    document_sha256: str
    sha256: str


@dataclass(frozen=True)
class Deployment:
    path: Path
    deployment_id: str
    deployment_generation: int
    coordinator_mode: str
    registry_id: str | None
    registry_generation: int | None
    target_set_id: str | None
    target_generation: int | None
    target_document_sha256: str | None
    target_topology_sha256: str | None
    required_acks: int | None
    coordinator: CoordinatorSpec | None
    targets: tuple[TargetSpec, ...]
    sha256: str


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MeshRenderError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _require_exact_keys(
    value: dict[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise MeshRenderError(f"{label} has invalid keys: {'; '.join(details)}")


def _validate_id(value: object, label: str) -> str:
    if type(value) is not str or ID_RE.fullmatch(value) is None:
        raise MeshRenderError(f"{label} must be a safe identifier")
    if unicodedata.normalize("NFC", value) != value:
        raise MeshRenderError(f"{label} must use NFC Unicode")
    return value


def _validate_runtime_name(value: object, label: str) -> str:
    if type(value) is not str or RUNTIME_NAME_RE.fullmatch(value) is None:
        raise MeshRenderError(
            f"{label} must be a lowercase Podman/systemd-safe name"
        )
    return value


def _validate_image(value: object, label: str) -> str:
    if type(value) is not str or IMAGE_RE.fullmatch(value) is None:
        raise MeshRenderError(
            f"{label} must be a repo@sha256 reference or full local sha256 image ID"
        )
    return value


def _validate_port(value: object, label: str) -> int:
    if type(value) is not int or not MIN_ROOTLESS_PORT <= value <= MAX_PORT:
        raise MeshRenderError(
            f"{label} must be an integer from {MIN_ROOTLESS_PORT} through {MAX_PORT}"
        )
    return value


def _validate_mesh_address(value: object, label: str) -> str:
    if type(value) is not str:
        raise MeshRenderError(f"{label} must be an RFC1918 IPv4 literal")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise MeshRenderError(f"{label} must be an RFC1918 IPv4 literal") from exc
    if not isinstance(address, ipaddress.IPv4Address) or str(address) != value:
        raise MeshRenderError(f"{label} must be a canonical RFC1918 IPv4 literal")
    network = next((item for item in RFC1918_NETWORKS if address in item), None)
    if (
        network is None
        or address == network.network_address
        or address == network.broadcast_address
        or address.is_loopback
        or address.is_multicast
        or address.is_unspecified
    ):
        raise MeshRenderError(f"{label} must be an RFC1918 IPv4 unicast address")
    return str(address)


def _absolute_path(value: str | os.PathLike[str], label: str) -> Path:
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise MeshRenderError(f"{label} path is invalid") from exc
    if not path.is_absolute():
        raise MeshRenderError(f"{label} path must be absolute")
    return Path(os.path.abspath(path))


def _validate_private_parent(path: Path, label: str) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise MeshRenderError(f"{label} parent directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise MeshRenderError(f"{label} parent must be a real directory")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise MeshRenderError(f"{label} parent is not owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise MeshRenderError(f"{label} parent must be owner-only mode 0700")


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    environment.pop("GIT_CONFIG_COUNT", None)
    for key in tuple(environment):
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            del environment[key]
    return environment


def _run_git(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MeshRenderError("cannot inspect Git containment for private paths") from exc


def _require_ignored_or_outside_git(path: Path, label: str) -> None:
    absolute = Path(os.path.realpath(path))
    result = _run_git(("-C", str(absolute.parent), "rev-parse", "--show-toplevel"))
    if result.returncode != 0:
        ancestor = absolute.parent
        while True:
            try:
                (ancestor / ".git").lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise MeshRenderError(
                    f"cannot inspect Git containment for {label}"
                ) from exc
            else:
                raise MeshRenderError(
                    f"cannot establish Git containment for {label}"
                )
            if ancestor == ancestor.parent:
                return
            ancestor = ancestor.parent
    try:
        worktree = Path(os.path.realpath(result.stdout.strip()))
        relative = absolute.relative_to(worktree).as_posix()
    except (OSError, ValueError) as exc:
        raise MeshRenderError(f"cannot establish Git containment for {label}") from exc
    tracked = _run_git(
        ("-C", str(worktree), "ls-files", "--error-unmatch", "--", relative)
    )
    if tracked.returncode == 0:
        raise MeshRenderError(f"{label} must not be tracked by Git")
    ignored = _run_git(
        (
            "-C",
            str(worktree),
            "check-ignore",
            "--verbose",
            "--no-index",
            "--",
            relative,
        )
    )
    if ignored.returncode != 0:
        raise MeshRenderError(f"{label} inside a Git worktree must be ignored")
    try:
        rule_description, _matched_path = ignored.stdout.rstrip("\n").split("\t", 1)
        rule_source, _line, _pattern = rule_description.rsplit(":", 2)
        rule_path = Path(rule_source)
        if rule_path.is_absolute():
            raise ValueError("global ignore source")
        canonical_rule = Path(os.path.realpath(worktree / rule_path))
        rule_relative = canonical_rule.relative_to(worktree).as_posix()
    except (OSError, ValueError) as exc:
        raise MeshRenderError(
            f"{label} must use a tracked worktree .gitignore rule"
        ) from exc
    tracked_rule = _run_git(
        (
            "-C",
            str(worktree),
            "ls-files",
            "--error-unmatch",
            "--",
            rule_relative,
        )
    )
    index_flags = _run_git(
        (
            "-C",
            str(worktree),
            "ls-files",
            "-v",
            "-z",
            "--",
            rule_relative,
        )
    )
    flag_records = [
        record for record in index_flags.stdout.encode("utf-8").split(b"\0") if record
    ]
    if (
        index_flags.returncode != 0
        or len(flag_records) != 1
        or len(flag_records[0]) < 3
        or flag_records[0][1:2] != b" "
    ):
        raise MeshRenderError(
            f"{label} cannot inspect tracked .gitignore index flags"
        )
    flag = chr(flag_records[0][0])
    if flag == "S" or flag.islower():
        raise MeshRenderError(
            f"{label} tracked .gitignore uses skip-worktree or assume-unchanged"
        )
    changed = _run_git(
        (
            "-C",
            str(worktree),
            "diff",
            "--quiet",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
            rule_relative,
        )
    )
    if tracked_rule.returncode != 0 or changed.returncode != 0:
        raise MeshRenderError(
            f"{label} requires a tracked, unchanged worktree .gitignore rule"
        )


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def _read_private_json(
    path: Path, label: str = "Podman-on-WireGuard deployment"
) -> tuple[dict[str, Any], bytes]:
    _validate_private_parent(path, label)
    _require_ignored_or_outside_git(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MeshRenderError(f"{label} must be a regular file")
        if before.st_nlink != 1:
            raise MeshRenderError(f"{label} must not be hard-linked")
        if hasattr(os, "getuid") and before.st_uid != os.getuid():
            raise MeshRenderError(f"{label} is not owned by the current user")
        if stat.S_IMODE(before.st_mode) & 0o077:
            raise MeshRenderError(f"{label} must not grant group or other access")
        if before.st_size > MAX_CONFIG_BYTES:
            raise MeshRenderError(f"{label} exceeds its size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONFIG_BYTES:
                raise MeshRenderError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
        if _metadata_identity(before) != _metadata_identity(after):
            raise MeshRenderError(f"{label} changed while being read")
        raw = b"".join(chunks)
    except FileNotFoundError as exc:
        raise MeshRenderError(f"{label} file was not found") from exc
    except MeshRenderError:
        raise
    except OSError as exc:
        raise MeshRenderError(f"{label} is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys
        )
    except UnicodeError as exc:
        raise MeshRenderError(f"{label} is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise MeshRenderError(
            f"invalid {label} JSON: "
            f"line {exc.lineno}, column {exc.colno}"
        ) from exc
    except (RecursionError, ValueError) as exc:
        raise MeshRenderError(f"invalid {label} JSON") from exc
    if type(payload) is not dict:
        raise MeshRenderError(f"{label} root must be a JSON object")
    return payload, raw


def _require_allowed_keys(
    value: dict[str, Any], required: set[str], allowed: set[str], label: str
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise MeshRenderError(f"{label} has invalid keys: {'; '.join(details)}")


def _load_target_topology(
    path_value: str | os.PathLike[str], target_set_id: str
) -> TargetTopology:
    path = _absolute_path(path_value, "authoritative sidecar targets")
    payload, raw = _read_private_json(path, "authoritative sidecar targets")
    _require_exact_keys(payload, SIDECAR_TARGET_ROOT_KEYS, "authoritative targets")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise MeshRenderError("authoritative targets must use schema_version 1")
    registry_id = _validate_id(
        payload["registry_id"], "authoritative targets registry_id"
    )
    registry_generation = payload["registry_generation"]
    target_generation = payload["target_generation"]
    if type(registry_generation) is not int or registry_generation < 0:
        raise MeshRenderError(
            "authoritative targets registry_generation must be non-negative"
        )
    if type(target_generation) is not int or target_generation < 1:
        raise MeshRenderError(
            "active authoritative targets require target_generation of at least one"
        )
    raw_sets = payload["target_sets"]
    if type(raw_sets) is not list or len(raw_sets) > MAX_TARGETS:
        raise MeshRenderError("authoritative target_sets must be a bounded JSON array")
    matches: list[dict[str, Any]] = []
    observed_set_ids: set[str] = set()
    for index, raw_set in enumerate(raw_sets):
        label = f"authoritative target set {index}"
        if type(raw_set) is not dict:
            raise MeshRenderError(f"{label} must be a JSON object")
        _require_exact_keys(raw_set, SIDECAR_TARGET_SET_KEYS, label)
        observed_id = _validate_id(raw_set["target_set_id"], f"{label} id")
        if observed_id in observed_set_ids:
            raise MeshRenderError("authoritative targets contain duplicate set IDs")
        observed_set_ids.add(observed_id)
        if observed_id == target_set_id:
            matches.append(raw_set)
    if len(matches) != 1:
        raise MeshRenderError(
            "deployment target_set_id must identify exactly one authoritative set"
        )
    raw_set = matches[0]
    if raw_set["tier"] != "mesh-only":
        raise MeshRenderError("deployment target set must be mesh-only")
    raw_targets = raw_set["targets"]
    if (
        type(raw_targets) is not list
        or len(raw_targets) < MIN_MESH_TARGETS
        or len(raw_targets) > MAX_TARGETS
    ):
        raise MeshRenderError(
            f"authoritative mesh set requires {MIN_MESH_TARGETS} through "
            f"{MAX_TARGETS} targets"
        )
    required_acks = raw_set["required_acks"]
    if (
        type(required_acks) is not int
        or required_acks <= len(raw_targets) // 2
        or required_acks > len(raw_targets)
    ):
        raise MeshRenderError(
            "authoritative mesh set requires a valid strict-majority acknowledgement"
        )
    targets: list[AuthoritativeTarget] = []
    for index, raw_target in enumerate(raw_targets):
        label = f"authoritative mesh target {index}"
        if type(raw_target) is not dict:
            raise MeshRenderError(f"{label} must be a JSON object")
        _require_allowed_keys(
            raw_target,
            SIDECAR_TARGET_REQUIRED_KEYS | {"sftp_port"},
            SIDECAR_TARGET_ALLOWED_KEYS,
            label,
        )
        targets.append(
            AuthoritativeTarget(
                target_id=_validate_id(raw_target["target_id"], f"{label} target_id"),
                failure_domain=_validate_id(
                    raw_target["failure_domain"], f"{label} failure_domain"
                ),
                mesh_address=_validate_mesh_address(
                    raw_target["mesh_address"], f"{label} mesh_address"
                ),
                sftp_port=_validate_port(
                    raw_target["sftp_port"], f"{label} sftp_port"
                ),
            )
        )
    if targets != sorted(targets, key=lambda item: item.target_id):
        raise MeshRenderError("authoritative mesh targets must be sorted by target_id")
    for values, label in (
        ([item.target_id for item in targets], "target IDs"),
        ([item.failure_domain for item in targets], "failure domains"),
        ([item.mesh_address for item in targets], "mesh addresses"),
    ):
        if len(values) != len(set(values)):
            raise MeshRenderError(
                f"authoritative mesh targets require distinct {label}"
            )
    topology_payload = {
        "registry_id": registry_id,
        "registry_generation": registry_generation,
        "target_generation": target_generation,
        "target_set_id": target_set_id,
        "required_acks": required_acks,
        "targets": [
            {
                "target_id": item.target_id,
                "failure_domain": item.failure_domain,
                "mesh_address": item.mesh_address,
                "sftp_port": item.sftp_port,
            }
            for item in targets
        ],
    }
    return TargetTopology(
        registry_id=registry_id,
        registry_generation=registry_generation,
        target_set_id=target_set_id,
        target_generation=target_generation,
        required_acks=required_acks,
        targets=tuple(targets),
        document_sha256=hashlib.sha256(raw).hexdigest(),
        sha256=hashlib.sha256(_canonical_json_bytes(topology_payload)).hexdigest(),
    )


def load_deployment(
    path_value: str | os.PathLike[str],
    targets_path_value: str | os.PathLike[str] | None = None,
) -> Deployment:
    path = _absolute_path(path_value, "Podman-on-WireGuard deployment")
    payload, _raw = _read_private_json(path)
    _require_exact_keys(payload, ROOT_KEYS, "Podman-on-WireGuard deployment")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise MeshRenderError(
            "Podman-on-WireGuard deployment must use schema_version "
            f"{SCHEMA_VERSION}"
        )
    deployment_id = _validate_id(payload["deployment_id"], "deployment_id")
    generation = payload["deployment_generation"]
    if type(generation) is not int or generation < 0:
        raise MeshRenderError("deployment_generation must be a non-negative integer")
    if payload["coordinator_mode"] != COORDINATOR_MODE:
        raise MeshRenderError(
            f"coordinator_mode must be exactly {COORDINATOR_MODE!r}"
        )
    raw_targets = payload["targets"]
    if type(raw_targets) is not list or len(raw_targets) > MAX_TARGETS:
        raise MeshRenderError("targets must be a bounded JSON array")

    raw_coordinator = payload["coordinator"]
    if generation == 0:
        if (
            raw_coordinator is not None
            or raw_targets
            or payload["target_set_id"] is not None
        ):
            raise MeshRenderError(
                "generation-zero Podman-on-WireGuard deployment must remain inert"
            )
        coordinator = None
        target_set_id = None
        topology = None
    else:
        target_set_id = _validate_id(payload["target_set_id"], "target_set_id")
        if type(raw_coordinator) is not dict:
            raise MeshRenderError("active deployment requires one coordinator object")
        _require_exact_keys(raw_coordinator, COORDINATOR_KEYS, "coordinator")
        coordinator = CoordinatorSpec(
            image=_validate_image(raw_coordinator["image"], "coordinator image"),
            unit_name=_validate_runtime_name(
                raw_coordinator["unit_name"], "coordinator unit_name"
            ),
            container_name=_validate_runtime_name(
                raw_coordinator["container_name"], "coordinator container_name"
            ),
        )
        if len(raw_targets) < MIN_MESH_TARGETS:
            raise MeshRenderError(
                f"active mesh deployment requires at least {MIN_MESH_TARGETS} "
                "SFTP targets"
            )
        if targets_path_value is None:
            raise MeshRenderError(
                "active deployment requires the authoritative sidecar targets path"
            )
        topology = _load_target_topology(targets_path_value, target_set_id)

    targets: list[TargetSpec] = []
    for index, raw_target in enumerate(raw_targets):
        label = f"target {index}"
        if type(raw_target) is not dict:
            raise MeshRenderError(f"{label} must be a JSON object")
        _require_exact_keys(raw_target, TARGET_KEYS, label)
        targets.append(
            TargetSpec(
                target_id=_validate_id(raw_target["target_id"], f"{label} target_id"),
                failure_domain=_validate_id(
                    raw_target["failure_domain"], f"{label} failure_domain"
                ),
                image=_validate_image(raw_target["image"], f"{label} image"),
                unit_name=_validate_runtime_name(
                    raw_target["unit_name"], f"{label} unit_name"
                ),
                container_name=_validate_runtime_name(
                    raw_target["container_name"], f"{label} container_name"
                ),
                mesh_address=_validate_mesh_address(
                    raw_target["mesh_address"], f"{label} mesh_address"
                ),
                published_port=_validate_port(
                    raw_target["published_port"], f"{label} published_port"
                ),
                container_port=_validate_port(
                    raw_target["container_port"], f"{label} container_port"
                ),
                repository_volume=_validate_runtime_name(
                    raw_target["repository_volume"], f"{label} repository_volume"
                ),
                host_key_secret=_validate_runtime_name(
                    raw_target["host_key_secret"], f"{label} host_key_secret"
                ),
                authorized_keys_secret=_validate_runtime_name(
                    raw_target["authorized_keys_secret"],
                    f"{label} authorized_keys_secret",
                ),
            )
        )

    if targets != sorted(targets, key=lambda item: item.target_id):
        raise MeshRenderError("targets must be sorted by target_id")

    def require_unique(values: Sequence[str], label: str) -> None:
        if len(values) != len(set(values)):
            raise MeshRenderError(f"targets require distinct {label}")

    require_unique([item.target_id for item in targets], "target IDs")
    require_unique([item.failure_domain for item in targets], "failure domains")
    require_unique([item.mesh_address for item in targets], "mesh addresses")
    require_unique([item.unit_name for item in targets], "unit names")
    require_unique([item.container_name for item in targets], "container names")
    require_unique([item.repository_volume for item in targets], "repository volumes")
    secret_names = [
        name
        for item in targets
        for name in (item.host_key_secret, item.authorized_keys_secret)
    ]
    require_unique(secret_names, "secret names")
    if coordinator is not None:
        if coordinator.unit_name in {item.unit_name for item in targets}:
            raise MeshRenderError("coordinator and targets require distinct unit names")
        if coordinator.container_name in {item.container_name for item in targets}:
            raise MeshRenderError(
                "coordinator and targets require distinct container names"
            )
    if topology is not None:
        deployment_topology = tuple(
            (
                item.target_id,
                item.failure_domain,
                item.mesh_address,
                item.published_port,
            )
            for item in targets
        )
        authoritative_topology = tuple(
            (
                item.target_id,
                item.failure_domain,
                item.mesh_address,
                item.sftp_port,
            )
            for item in topology.targets
        )
        if deployment_topology != authoritative_topology:
            raise MeshRenderError(
                "deployment targets do not exactly match the authoritative mesh topology"
            )

    return Deployment(
        path=path,
        deployment_id=deployment_id,
        deployment_generation=generation,
        coordinator_mode=COORDINATOR_MODE,
        registry_id=(topology.registry_id if topology is not None else None),
        registry_generation=(
            topology.registry_generation if topology is not None else None
        ),
        target_set_id=target_set_id,
        target_generation=(topology.target_generation if topology is not None else None),
        target_document_sha256=(
            topology.document_sha256 if topology is not None else None
        ),
        target_topology_sha256=(topology.sha256 if topology is not None else None),
        required_acks=(topology.required_acks if topology is not None else None),
        coordinator=coordinator,
        targets=tuple(targets),
        sha256=hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
    )


def _coordinator_review(deployment: Deployment) -> str:
    assert deployment.coordinator is not None
    coordinator = deployment.coordinator
    return "\n".join(
        (
            "# REVIEW ONLY: this suffix is not recognized by Quadlet or systemd.",
            "# The coordinator stays host-controlled until exact policy-derived",
            "# mounts, schedules, restore evidence, and secret rotation are bound.",
            "[CoordinatorReview]",
            f"DeploymentId={deployment.deployment_id}",
            f"DeploymentGeneration={deployment.deployment_generation}",
            f"CoordinatorMode={deployment.coordinator_mode}",
            f"Image={coordinator.image}",
            f"UnitName={coordinator.unit_name}",
            f"ContainerName={coordinator.container_name}",
            "QuadletActivationSupported=false",
            "AutomaticFailoverSupported=false",
            "",
        )
    )


def _target_volume(target: TargetSpec) -> str:
    return "\n".join(
        (
            "[Unit]",
            f"Description=Persistent repository volume for {target.target_id}",
            "",
            "[Volume]",
            f"VolumeName={target.repository_volume}",
            "Driver=local",
            f"Label=io.traction-control.sidecar.target={target.target_id}",
            "",
        )
    )


def _target_container(target: TargetSpec) -> str:
    volume_unit = f"{target.unit_name}-repository.volume"
    return "\n".join(
        (
            "# REVIEW ONLY: start only after the pinned image contract and",
            "# minimal sshd capability set pass native rootless Linux tests.",
            "# Exact RFC1918 binding is not WireGuard membership or route attestation.",
            "[Unit]",
            f"Description=Mesh-only SFTP sidecar target {target.target_id}",
            "",
            "[Container]",
            f"Image={target.image}",
            f"ContainerName={target.container_name}",
            "Pull=never",
            "Network=bridge",
            f"PublishPort={target.mesh_address}:{target.published_port}:"
            f"{target.container_port}/tcp",
            "ReadOnly=true",
            "ReadOnlyTmpfs=false",
            "NoNewPrivileges=true",
            "DropCapability=ALL",
            "AddCapability=CHOWN",
            "AddCapability=DAC_OVERRIDE",
            "AddCapability=SETGID",
            "AddCapability=SETUID",
            "AddCapability=SYS_CHROOT",
            "Tmpfs=/run:rw,nodev,nosuid,noexec,size=16m",
            "Tmpfs=/tmp:rw,nodev,nosuid,noexec,size=16m",
            f"Volume={volume_unit}:/srv/portfolio-sidecar/repository:"
            "rw,nodev,nosuid,noexec,Z",
            f"Secret=source={target.host_key_secret},target=sidecar-host-key,"
            "type=mount,uid=0,gid=0,mode=0400",
            f"Secret=source={target.authorized_keys_secret},"
            "target=sidecar-authorized-keys,type=mount,uid=0,gid=0,mode=0400",
            f"Environment=SIDECAR_SFTP_PORT={target.container_port}",
            "Environment=SIDECAR_SFTP_REPOSITORY=/srv/portfolio-sidecar/repository",
            "Environment=SIDECAR_SFTP_HOST_KEY=/run/secrets/sidecar-host-key",
            "Environment=SIDECAR_SFTP_AUTHORIZED_KEYS="
            "/run/secrets/sidecar-authorized-keys",
            f"Label=io.traction-control.sidecar.target={target.target_id}",
            f"Label=io.traction-control.sidecar.failure-domain={target.failure_domain}",
            "",
            "[Service]",
            "Restart=on-failure",
            "RestartSec=5s",
            "TimeoutStartSec=60s",
            "TimeoutStopSec=30s",
            "",
        )
    )


def _write_new_file(directory_descriptor: int, name: str, content: bytes) -> str:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_descriptor)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise MeshRenderError(
                    "could not write rendered Podman-on-WireGuard artifact"
                )
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise MeshRenderError(f"rendered artifact already exists: {name}") from exc
    except MeshRenderError:
        raise
    except OSError as exc:
        raise MeshRenderError(f"cannot write rendered artifact: {name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


def _open_new_output(output_value: str | os.PathLike[str]) -> tuple[Path, int]:
    output = _absolute_path(output_value, "Podman-on-WireGuard output")
    _validate_private_parent(output, "Podman-on-WireGuard output")
    _require_ignored_or_outside_git(output, "Podman-on-WireGuard output")
    try:
        os.mkdir(output, mode=0o700)
    except FileExistsError as exc:
        raise MeshRenderError("render refuses to overwrite an existing output path") from exc
    except OSError as exc:
        raise MeshRenderError(
            "cannot create owner-only Podman-on-WireGuard output"
        ) from exc
    try:
        descriptor = os.open(
            output,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
        ):
            raise MeshRenderError(
                "Podman-on-WireGuard output is not an owner-only directory"
            )
        return output, descriptor
    except BaseException:
        try:
            os.rmdir(output)
        except OSError:
            pass
        raise


def _manifest_base(deployment: Deployment, bundle_role: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": deployment.deployment_id,
        "deployment_generation": deployment.deployment_generation,
        "deployment_sha256": deployment.sha256,
        "registry_id": deployment.registry_id,
        "registry_generation": deployment.registry_generation,
        "target_set_id": deployment.target_set_id,
        "target_generation": deployment.target_generation,
        "target_document_sha256": deployment.target_document_sha256,
        "target_topology_sha256": deployment.target_topology_sha256,
        "required_acks": deployment.required_acks,
        "coordinator_mode": deployment.coordinator_mode,
        "bundle_role": bundle_role,
        "activation_ready": False,
        "activation_performed": False,
        "podman_invoked": False,
        "systemctl_invoked": False,
        "wireguard_configured": False,
        "wireguard_attested": False,
        "secrets_created": False,
        "full_sidecar_target_validation_performed": False,
        "host_key_known_hosts_binding_validated": False,
        "repository_capacity_boundary_validated": False,
        "platform": "linux-native",
        "platform_boundary": (
            "the host must own the WireGuard bind address; a macOS Podman "
            "machine is verification-only until host/VM routing is proven"
        ),
    }


def render_target_bundle(
    deployment: Deployment,
    target_id: str,
    output_value: str | os.PathLike[str],
) -> Path:
    if deployment.deployment_generation == 0 or deployment.coordinator is None:
        raise MeshRenderError("generation-zero Podman-on-WireGuard deployment is inert")
    selected = tuple(item for item in deployment.targets if item.target_id == target_id)
    if len(selected) != 1:
        raise MeshRenderError("target-id must identify exactly one deployment target")
    target = selected[0]
    output, descriptor = _open_new_output(output_value)
    try:
        volume_name = f"{target.unit_name}-repository.volume"
        container_name = f"{target.unit_name}.container"
        volume_sha = _write_new_file(
            descriptor, volume_name, _target_volume(target).encode("utf-8")
        )
        container_sha = _write_new_file(
            descriptor, container_name, _target_container(target).encode("utf-8")
        )
        manifest = _manifest_base(deployment, "sftp-target-review")
        manifest.update(
            {
                "target": {
                    "target_id": target.target_id,
                    "failure_domain": target.failure_domain,
                    "mesh_address": target.mesh_address,
                    "published_port": target.published_port,
                    "container_port": target.container_port,
                    "image": target.image,
                    "unit": container_name,
                    "volume_unit": volume_name,
                    "repository_volume": target.repository_volume,
                    "secret_names": [
                        target.host_key_secret,
                        target.authorized_keys_secret,
                    ],
                },
                "operator_prerequisites": [
                    "pinned image implements the declared SFTP entrypoint contract",
                    "minimal sshd capabilities pass native rootless Linux tests",
                    "the full sidecar registry and target loader validates every target and credential file",
                    "each target host-key fingerprint is bound to the coordinator known_hosts file",
                    "named Podman secrets exist and secret rotation recreates the container",
                    "host owns and firewall-scopes the exact WireGuard bind address",
                    "repository storage has a monitored quota or dedicated capacity boundary",
                    "resource limits are reviewed for the target host",
                ],
                "artifacts": [
                    {
                        "path": volume_name,
                        "role": "target-volume",
                        "sha256": volume_sha,
                    },
                    {
                        "path": container_name,
                        "role": "target-container-review",
                        "sha256": container_sha,
                    },
                ],
            }
        )
        _write_new_file(descriptor, "manifest.json", _pretty_json_bytes(manifest))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return output


def render_coordinator_review_bundle(
    deployment: Deployment, output_value: str | os.PathLike[str]
) -> Path:
    if deployment.deployment_generation == 0 or deployment.coordinator is None:
        raise MeshRenderError("generation-zero Podman-on-WireGuard deployment is inert")
    output, descriptor = _open_new_output(output_value)
    try:
        name = f"{deployment.coordinator.unit_name}.coordinator-review"
        sha256 = _write_new_file(
            descriptor, name, _coordinator_review(deployment).encode("utf-8")
        )
        manifest = _manifest_base(deployment, "coordinator-review")
        manifest.update(
            {
                "coordinator": {
                    "image": deployment.coordinator.image,
                    "artifact": name,
                    "activatable": False,
                    "boundary": (
                        "policy-derived mounts, scheduling, evidence, and secret "
                        "rotation are not rendered yet"
                    ),
                },
                "target_count": len(deployment.targets),
                "artifacts": [
                    {
                        "path": name,
                        "role": "coordinator-review",
                        "sha256": sha256,
                    }
                ],
            }
        )
        _write_new_file(descriptor, "manifest.json", _pretty_json_bytes(manifest))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return output


def initialize_config(path_value: str | os.PathLike[str]) -> Deployment:
    path = _absolute_path(path_value, "Podman-on-WireGuard deployment")
    _validate_private_parent(path, "Podman-on-WireGuard deployment")
    _require_ignored_or_outside_git(path, "Podman-on-WireGuard deployment")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": "local-podman-mesh",
        "deployment_generation": 0,
        "coordinator_mode": COORDINATOR_MODE,
        "target_set_id": None,
        "coordinator": None,
        "targets": [],
    }
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        content = _pretty_json_bytes(payload)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise MeshRenderError(
                    "cannot initialize Podman-on-WireGuard deployment"
                )
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise MeshRenderError("init-config refuses to overwrite an existing file") from exc
    except MeshRenderError:
        raise
    except OSError as exc:
        raise MeshRenderError(
            "cannot initialize Podman-on-WireGuard deployment"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        return load_deployment(path)
    except BaseException:
        # The file was published with O_EXCL and valid canonical content.  Leave
        # it for owner inspection rather than unlinking an identity we did not
        # retain across post-publication validation.
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or render inactive rootless Podman-on-WireGuard "
            "sidecar target bundles."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init-config", help="Create one ignored owner-only inert local document."
    )
    init_parser.add_argument("--deployment", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate an ignored owner-only deployment document."
    )
    validate_parser.add_argument("--deployment", required=True)
    validate_parser.add_argument(
        "--targets",
        help="Authoritative ignored sidecar targets; required above generation zero.",
    )

    render_parser = subparsers.add_parser(
        "render", help="Render one new inactive SFTP-target review bundle."
    )
    render_parser.add_argument("--deployment", required=True)
    render_parser.add_argument("--targets", required=True)
    render_parser.add_argument("--target-id", required=True)
    render_parser.add_argument("--output", required=True)

    coordinator_parser = subparsers.add_parser(
        "render-coordinator-review",
        help="Render the non-Quadlet coordinator boundary for review.",
    )
    coordinator_parser.add_argument("--deployment", required=True)
    coordinator_parser.add_argument("--targets", required=True)
    coordinator_parser.add_argument("--output", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _build_parser().parse_args(arguments)
    try:
        if parsed.command == "init-config":
            deployment = initialize_config(parsed.deployment)
            print(
                "initialized inert Podman-on-WireGuard deployment "
                f"generation {deployment.deployment_generation}"
            )
            return 0
        deployment = load_deployment(parsed.deployment, parsed.targets)
        if parsed.command == "validate":
            state = "inert" if deployment.deployment_generation == 0 else "reviewable"
            print(
                f"validated {state} Podman-on-WireGuard deployment generation "
                f"{deployment.deployment_generation} with {len(deployment.targets)} targets"
            )
            return 0
        if parsed.command == "render-coordinator-review":
            output = render_coordinator_review_bundle(deployment, parsed.output)
            print(f"rendered inactive coordinator review bundle: {output}")
            return 0
        output = render_target_bundle(deployment, parsed.target_id, parsed.output)
        print(
            f"rendered one inactive Podman-on-WireGuard target review bundle: {output}"
        )
        return 0
    except MeshRenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
