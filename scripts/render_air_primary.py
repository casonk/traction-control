#!/usr/bin/env python3
"""Render an inert Air-primary service bundle across sibling utility repos.

The coordinator owns only private input staging and an aggregate manifest.
Clockwork, Snowbridge, and wiring-harness remain authoritative for their own
outputs.  No command in this module activates launchd, Caddy, SMB, or PF.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import plistlib
import pwd
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "air-primary.local.toml"
EXAMPLE_CONFIG = REPO_ROOT / "config" / "air-primary.example.toml"
ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "air-primary"
PLACEHOLDER = re.compile(
    r"(?:<[^>]+>|\$\{|\b(?:change[-_ ]?me|replace[-_ ]?me|todo)\b|example\.(?:com|net|org))",
    re.IGNORECASE,
)
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
SAFE_ACCOUNT = re.compile(r"[A-Za-z0-9._-]+\Z")
UTUN = re.compile(r"utun[0-9]+\Z")
RFC1918 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
EXIT_CODES = {
    "unsafe_configuration": 2,
    "missing_prerequisite": 3,
    "api_mismatch": 4,
    "render_failure": 5,
}
NATIVE_SMB_DISABLED_REASON = (
    "disabled by snowbridge.native_smb_enabled=false; native SMB prerequisites, "
    "Snowbridge macOS SMB-plan rendering, and SMB artifacts were intentionally omitted"
)


class CoordinatorError(RuntimeError):
    """A classified, operator-actionable coordinator failure."""

    def __init__(self, category: str, messages: str | Sequence[str]):
        if category not in EXIT_CODES:
            raise ValueError(f"unsupported failure category: {category}")
        if isinstance(messages, str):
            messages = [messages]
        self.category = category
        self.messages = tuple(messages)
        super().__init__("; ".join(self.messages))

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.category]

    def report(self) -> dict[str, Any]:
        return {
            "ok": False,
            "category": self.category,
            "messages": list(self.messages),
        }


@dataclass(frozen=True)
class AirConfig:
    source: Path
    generation: int
    deployment_id: str
    wireguard_interface: str
    air_address: str
    mini_address: str
    pro_address: str
    clockwork_repo: Path
    snowbridge_repo: Path
    wiring_repo: Path
    runtime_python: Path
    caddy_binary: Path
    clockwork_python: Path
    clockwork_environment_file: Path
    share_name: str
    share_path: Path
    expected_account: str
    inventory_file: Path | None
    snowbridge_native_smb_enabled: bool
    snowbridge_web_enabled: bool
    certs_dir: Path


def _fail(category: str, message: str) -> None:
    raise CoordinatorError(category, message)


def _strict_table(
    value: object,
    *,
    name: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("unsafe_configuration", f"{name} must be a TOML table")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown:
        _fail(
            "unsafe_configuration",
            f"{name} contains unsupported fields: {', '.join(sorted(unknown))}",
        )
    if missing:
        _fail(
            "unsafe_configuration",
            f"{name} is missing required fields: {', '.join(sorted(missing))}",
        )
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("unsafe_configuration", f"{field} must be a non-empty string")
    if PLACEHOLDER.search(value):
        _fail("unsafe_configuration", f"{field} contains a placeholder")
    if any(ord(character) < 0x20 for character in value):
        _fail("unsafe_configuration", f"{field} contains a control character")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("unsafe_configuration", f"{field} must be an integer")
    return value


def _absolute_path(value: object, field: str) -> Path:
    raw = _string(value, field)
    path = Path(raw)
    if not path.is_absolute() or Path(os.path.normpath(raw)) != path:
        _fail("unsafe_configuration", f"{field} must be an absolute canonical path")
    return path


def _wireguard_32(value: object, field: str) -> str:
    raw = _string(value, field)
    try:
        address = ipaddress.ip_interface(raw)
    except ValueError as error:
        raise CoordinatorError(
            "unsafe_configuration", f"{field} must be a valid IPv4 /32"
        ) from error
    if (
        not isinstance(address, ipaddress.IPv4Interface)
        or address.network.prefixlen != 32
    ):
        _fail("unsafe_configuration", f"{field} must be an IPv4 /32")
    if not any(address.ip in network for network in RFC1918):
        _fail("unsafe_configuration", f"{field} must use an RFC1918 address")
    return str(address)


def _private_file(
    path: Path, description: str, *, missing_is_prerequisite: bool
) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        category = (
            "missing_prerequisite"
            if missing_is_prerequisite
            else "unsafe_configuration"
        )
        raise CoordinatorError(
            category, f"{description} does not exist: {path}"
        ) from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        _fail(
            "unsafe_configuration",
            f"{description} must be a regular, non-symlink file: {path}",
        )
    if details.st_nlink != 1:
        _fail(
            "unsafe_configuration",
            f"{description} must have exactly one hard link: {path}",
        )
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        _fail(
            "unsafe_configuration",
            f"{description} must be owned by the current user: {path}",
        )
    if stat.S_IMODE(details.st_mode) & 0o077:
        _fail(
            "unsafe_configuration",
            f"{description} must be owner-only (chmod 600): {path}",
        )
    return details


def _private_directory(path: Path, description: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise CoordinatorError(
            "missing_prerequisite", f"{description} does not exist: {path}"
        ) from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        _fail("unsafe_configuration", f"{description} must be a real directory: {path}")
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        _fail(
            "unsafe_configuration",
            f"{description} must be owned by the current user: {path}",
        )
    if stat.S_IMODE(details.st_mode) & 0o077:
        _fail(
            "unsafe_configuration",
            f"{description} must be owner-only (chmod 700): {path}",
        )
    return details


def _run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.fspath(part) for part in command],
        cwd=cwd,
        env=dict(env) if env else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return _run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-C",
            repo,
            *arguments,
        ],
        env=environment,
    )


def _assert_local_config_private(path: Path, repo_root: Path) -> None:
    details = _private_file(
        path, "Air-primary local config", missing_is_prerequisite=False
    )
    if details.st_size > 256 * 1024:
        _fail("unsafe_configuration", "Air-primary local config exceeds 256 KiB")
    try:
        path.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
    except ValueError:
        return
    current = path.parent
    while True:
        ancestor = current.lstat()
        if stat.S_ISLNK(ancestor.st_mode) or stat.S_IMODE(ancestor.st_mode) & 0o022:
            _fail(
                "unsafe_configuration",
                f"local config has an untrusted ancestor: {current}",
            )
        if current == repo_root:
            break
        current = current.parent
    tracked = _git(repo_root, "ls-files", "--error-unmatch", os.fspath(path))
    if tracked.returncode == 0:
        _fail(
            "unsafe_configuration", f"local config must not be tracked by Git: {path}"
        )
    ignored = _git(repo_root, "check-ignore", "-q", os.fspath(path))
    if ignored.returncode != 0:
        _fail("unsafe_configuration", f"local config must be ignored by Git: {path}")


def _assert_private_destination(path: Path) -> None:
    """Refuse an init target that Git could publish accidentally."""

    existing = path.parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    worktree = _git(existing, "rev-parse", "--show-toplevel")
    if worktree.returncode != 0:
        return
    top = Path(worktree.stdout.strip())
    tracked = _git(top, "ls-files", "--error-unmatch", os.fspath(path))
    if tracked.returncode == 0:
        _fail(
            "unsafe_configuration",
            f"local config destination is tracked by Git: {path}",
        )
    ignored = _git(top, "check-ignore", "-q", os.fspath(path))
    if ignored.returncode != 0:
        _fail(
            "unsafe_configuration",
            f"local config destination is not ignored by Git: {path}",
        )


def load_config(path: Path, *, repo_root: Path = REPO_ROOT) -> AirConfig:
    path = path.absolute()
    _assert_local_config_private(path, repo_root)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CoordinatorError(
            "unsafe_configuration", f"cannot parse local config: {error}"
        ) from error

    root = _strict_table(
        data,
        name="root",
        required={
            "schema_version",
            "mode",
            "generation",
            "deployment_id",
            "network",
            "repositories",
            "runtime",
            "clockwork",
            "snowbridge",
            "wiring_harness",
        },
    )
    if _integer(root["schema_version"], "schema_version") != 1:
        _fail("unsafe_configuration", "schema_version must be 1")
    if root["mode"] != "render-only":
        _fail("unsafe_configuration", "mode must remain render-only")
    generation = _integer(root["generation"], "generation")
    if generation < 1:
        _fail("unsafe_configuration", "generation must be a positive integer")
    deployment_id = _string(root["deployment_id"], "deployment_id")
    if SAFE_ID.fullmatch(deployment_id) is None:
        _fail(
            "unsafe_configuration", "deployment_id must be a safe lowercase identifier"
        )

    network = _strict_table(
        root["network"],
        name="network",
        required={"wireguard_interface", "air_address", "mini_address", "pro_address"},
    )
    interface = _string(network["wireguard_interface"], "network.wireguard_interface")
    if UTUN.fullmatch(interface) is None:
        _fail(
            "unsafe_configuration",
            "network.wireguard_interface must match utun<number>",
        )
    addresses = {
        key: _wireguard_32(network[key], f"network.{key}")
        for key in ("air_address", "mini_address", "pro_address")
    }
    if len(set(addresses.values())) != 3:
        _fail(
            "unsafe_configuration",
            "Air, mini, and pro WireGuard /32 addresses must be distinct",
        )

    repositories = _strict_table(
        root["repositories"],
        name="repositories",
        required={"clockwork", "snowbridge", "wiring_harness"},
    )
    runtime = _strict_table(
        root["runtime"], name="runtime", required={"python", "caddy_binary"}
    )
    clockwork = _strict_table(
        root["clockwork"],
        name="clockwork",
        required={
            "python",
            "environment_file",
            "backend_host",
            "backend_port",
            "edge_port",
        },
    )
    if clockwork["backend_host"] != "127.0.0.1":
        _fail("unsafe_configuration", "clockwork.backend_host must be 127.0.0.1")
    if _integer(clockwork["backend_port"], "clockwork.backend_port") != 5001:
        _fail("unsafe_configuration", "clockwork.backend_port must be 5001")
    if _integer(clockwork["edge_port"], "clockwork.edge_port") != 8443:
        _fail("unsafe_configuration", "clockwork.edge_port must be 8443")

    snowbridge = _strict_table(
        root["snowbridge"],
        name="snowbridge",
        required={
            "share_name",
            "share_path",
            "expected_account",
            "native_smb_enabled",
            "web_backend_8080_enabled",
            "web_backend_port",
            "web_edge_port",
        },
        optional={"inventory_file"},
    )
    share_name = _string(snowbridge["share_name"], "snowbridge.share_name")
    if SAFE_ID.fullmatch(share_name) is None:
        _fail("unsafe_configuration", "snowbridge.share_name must be a safe identifier")
    account = _string(snowbridge["expected_account"], "snowbridge.expected_account")
    if SAFE_ACCOUNT.fullmatch(account) is None:
        _fail(
            "unsafe_configuration",
            "snowbridge.expected_account is not a safe account name",
        )
    native_smb_enabled = snowbridge["native_smb_enabled"]
    if not isinstance(native_smb_enabled, bool):
        _fail(
            "unsafe_configuration",
            "snowbridge.native_smb_enabled must be boolean",
        )
    web_enabled = snowbridge["web_backend_8080_enabled"]
    if not isinstance(web_enabled, bool):
        _fail(
            "unsafe_configuration",
            "snowbridge.web_backend_8080_enabled must be boolean",
        )
    if _integer(snowbridge["web_backend_port"], "snowbridge.web_backend_port") != 8080:
        _fail("unsafe_configuration", "snowbridge.web_backend_port must be 8080")
    if _integer(snowbridge["web_edge_port"], "snowbridge.web_edge_port") != 8444:
        _fail("unsafe_configuration", "snowbridge.web_edge_port must be 8444")

    wiring = _strict_table(
        root["wiring_harness"], name="wiring_harness", required={"certs_dir"}
    )
    inventory = snowbridge.get("inventory_file")
    return AirConfig(
        source=path,
        generation=generation,
        deployment_id=deployment_id,
        wireguard_interface=interface,
        air_address=addresses["air_address"],
        mini_address=addresses["mini_address"],
        pro_address=addresses["pro_address"],
        clockwork_repo=_absolute_path(
            repositories["clockwork"], "repositories.clockwork"
        ),
        snowbridge_repo=_absolute_path(
            repositories["snowbridge"], "repositories.snowbridge"
        ),
        wiring_repo=_absolute_path(
            repositories["wiring_harness"], "repositories.wiring_harness"
        ),
        runtime_python=_absolute_path(runtime["python"], "runtime.python"),
        caddy_binary=_absolute_path(runtime["caddy_binary"], "runtime.caddy_binary"),
        clockwork_python=_absolute_path(clockwork["python"], "clockwork.python"),
        clockwork_environment_file=_absolute_path(
            clockwork["environment_file"], "clockwork.environment_file"
        ),
        share_name=share_name,
        share_path=_absolute_path(snowbridge["share_path"], "snowbridge.share_path"),
        expected_account=account,
        inventory_file=(
            _absolute_path(inventory, "snowbridge.inventory_file")
            if inventory is not None
            else None
        ),
        snowbridge_native_smb_enabled=native_smb_enabled,
        snowbridge_web_enabled=web_enabled,
        certs_dir=_absolute_path(wiring["certs_dir"], "wiring_harness.certs_dir"),
    )


def _safe_write(path: Path, payload: str | bytes) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _toml_string(value: str | Path) -> str:
    return json.dumps(os.fspath(value), ensure_ascii=True)


class AirPrimaryCoordinator:
    """Strict render-only orchestration over the three owning CLIs."""

    def __init__(self, config: AirConfig, *, repo_root: Path = REPO_ROOT):
        self.config = config
        self.repo_root = repo_root.resolve()

    @property
    def generation_dir(self) -> Path:
        return (
            self.repo_root
            / "artifacts"
            / "air-primary"
            / f"generation-{self.config.generation}"
        )

    @property
    def snowbridge_output(self) -> Path:
        return (
            self.config.snowbridge_repo
            / "artifacts"
            / "traction-control-air-primary"
            / f"generation-{self.config.generation}"
        )

    def _validate_repositories(self) -> dict[str, Any]:
        expected = {
            "clockwork": self.repo_root.parent / "clockwork",
            "snowbridge": self.repo_root.parent / "snowbridge",
            "wiring-harness": self.repo_root.parent / "wiring-harness",
        }
        configured = {
            "clockwork": self.config.clockwork_repo,
            "snowbridge": self.config.snowbridge_repo,
            "wiring-harness": self.config.wiring_repo,
        }
        renderer_files = {
            "clockwork": [
                "src/clockwork/__init__.py",
                "src/clockwork/__main__.py",
                "src/clockwork/cli.py",
                "src/clockwork/manifest.py",
                "src/clockwork/model.py",
                "src/clockwork/render.py",
                "scripts/run_clockwork_web_macos.sh",
            ],
            "snowbridge": (
                ["scripts/macos_smb_plan.py"]
                if self.config.snowbridge_native_smb_enabled
                else []
            ),
            "wiring-harness": [
                "scripts/render_macos_private_edge.py",
                "scripts/site_registry.py",
            ],
        }
        snapshots: dict[str, Any] = {}
        for name, path in configured.items():
            expected_path = expected[name].resolve(strict=False)
            if path.resolve(strict=False) != expected_path:
                _fail(
                    "unsafe_configuration",
                    f"repositories.{name.replace('-', '_')} must be exact sibling {expected_path}",
                )
            try:
                details = path.lstat()
            except FileNotFoundError as error:
                raise CoordinatorError(
                    "missing_prerequisite",
                    f"required sibling repository is missing: {path}",
                ) from error
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                _fail(
                    "unsafe_configuration",
                    f"sibling repository must be a real directory: {path}",
                )
            top = _git(path, "rev-parse", "--show-toplevel")
            if (
                top.returncode != 0
                or Path(top.stdout.strip()).resolve(strict=False) != expected_path
            ):
                _fail(
                    "unsafe_configuration",
                    f"path is not the exact Git worktree root: {path}",
                )
            head = _git(path, "rev-parse", "HEAD")
            status = _git(path, "status", "--porcelain")
            if head.returncode != 0 or status.returncode != 0:
                _fail(
                    "missing_prerequisite",
                    f"cannot inspect sibling Git metadata: {path}",
                )
            sources: dict[str, str] = {}
            for relative in renderer_files[name]:
                source = path / relative
                if not source.is_file() or source.is_symlink():
                    _fail(
                        "api_mismatch",
                        f"required sibling renderer source is missing: {source}",
                    )
                sources[relative] = _sha256(source)
            snapshots[name] = {
                "path": os.fspath(path),
                "head": head.stdout.strip(),
                "dirty": bool(status.stdout.strip()),
                "renderer_sha256": sources,
            }
        return snapshots

    def _probe_api(self) -> None:
        probes = [
            (
                [self.config.runtime_python, "-m", "clockwork", "install", "--help"],
                self.config.clockwork_repo,
                {"--manifest", "--target", "--unit-dir", "launchd-user"},
                {"PYTHONPATH": os.fspath(self.config.clockwork_repo / "src")},
            ),
        ]
        if self.config.snowbridge_native_smb_enabled:
            probes.extend(
                [
                    (
                        [
                            self.config.runtime_python,
                            self.config.snowbridge_repo / "scripts/macos_smb_plan.py",
                            "--help",
                        ],
                        self.config.snowbridge_repo,
                        {"--config", "render"},
                        {},
                    ),
                    (
                        [
                            self.config.runtime_python,
                            self.config.snowbridge_repo / "scripts/macos_smb_plan.py",
                            "render",
                            "--help",
                        ],
                        self.config.snowbridge_repo,
                        {"--inventory-file", "--output"},
                        {},
                    ),
                ]
            )
        probes.append(
            (
                [
                    self.config.runtime_python,
                    self.config.wiring_repo / "scripts/render_macos_private_edge.py",
                    "--help",
                ],
                self.config.wiring_repo,
                {
                    "--services",
                    "--certs-dir",
                    "--output-dir",
                    "--caddy-binary",
                    "--validate-caddy",
                },
                {"PYTHONPATH": os.fspath(self.config.wiring_repo / "scripts")},
            )
        )
        for command, cwd, tokens, additions in probes:
            environment = os.environ.copy()
            environment.update(additions)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = _run(command, cwd=cwd, env=environment)
            output = result.stdout + result.stderr
            missing = sorted(token for token in tokens if token not in output)
            if result.returncode != 0 or missing:
                detail = (
                    f"; missing help tokens: {', '.join(missing)}" if missing else ""
                )
                _fail(
                    "api_mismatch",
                    f"sibling CLI contract probe failed: {command[1]}{detail}",
                )

    def _preflight(self) -> dict[str, Any]:
        snapshots = self._validate_repositories()
        missing: list[str] = []
        unsafe: list[str] = []

        def executable(path: Path, description: str) -> None:
            if not path.exists():
                missing.append(f"{description} does not exist: {path}")
            elif not path.is_file() or not os.access(path, os.X_OK):
                unsafe.append(
                    f"{description} must resolve to an executable regular file: {path}"
                )

        executable(self.config.runtime_python, "coordinator Python")
        executable(self.config.clockwork_python, "Clockwork web Python")
        executable(self.config.caddy_binary, "Caddy binary")
        executable(Path("/usr/bin/python3"), "launchd environment-loader Python")

        try:
            environment_details = _private_file(
                self.config.clockwork_environment_file,
                "Clockwork environment file",
                missing_is_prerequisite=True,
            )
            if environment_details.st_size > 64 * 1024:
                unsafe.append("Clockwork environment file exceeds 64 KiB")
            secret = None
            for (
                raw_line
            ) in self.config.clockwork_environment_file.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "CLOCKWORK_WEB_SECRET":
                    secret = value.strip().strip("\"'")
            if secret is None:
                missing.append("Clockwork environment file lacks CLOCKWORK_WEB_SECRET")
            elif len(secret) < 32:
                unsafe.append(
                    "CLOCKWORK_WEB_SECRET must contain at least 32 characters"
                )
        except CoordinatorError as error:
            (missing if error.category == "missing_prerequisite" else unsafe).extend(
                error.messages
            )
        except (OSError, UnicodeDecodeError) as error:
            unsafe.append(f"cannot read Clockwork environment file safely: {error}")

        if self.config.snowbridge_native_smb_enabled:
            try:
                share = _private_directory(
                    self.config.share_path, "Snowbridge share directory"
                )
                if stat.S_IMODE(share.st_mode) != 0o700:
                    unsafe.append(
                        "Snowbridge share directory must have exact mode 0700"
                    )
                current_account = pwd.getpwuid(os.getuid()).pw_name
                if self.config.expected_account != current_account:
                    unsafe.append(
                        "snowbridge.expected_account must be the current macOS account"
                    )
            except CoordinatorError as error:
                (
                    missing if error.category == "missing_prerequisite" else unsafe
                ).extend(error.messages)

            if self.config.inventory_file is not None:
                try:
                    inventory_details = _private_file(
                        self.config.inventory_file,
                        "Snowbridge inventory fixture",
                        missing_is_prerequisite=True,
                    )
                    if inventory_details.st_size > 1024 * 1024:
                        unsafe.append("Snowbridge inventory fixture exceeds 1 MiB")
                except CoordinatorError as error:
                    (
                        missing if error.category == "missing_prerequisite" else unsafe
                    ).extend(error.messages)

        try:
            _private_directory(
                self.config.certs_dir, "wiring-harness certificate directory"
            )
        except CoordinatorError as error:
            (missing if error.category == "missing_prerequisite" else unsafe).extend(
                error.messages
            )
        else:
            for name in ("server.crt", "server.key", "ca.crt"):
                try:
                    _private_file(
                        self.config.certs_dir / name,
                        f"wiring-harness certificate material {name}",
                        missing_is_prerequisite=True,
                    )
                except CoordinatorError as error:
                    (
                        missing if error.category == "missing_prerequisite" else unsafe
                    ).extend(error.messages)

        if unsafe:
            raise CoordinatorError("unsafe_configuration", unsafe)
        if missing:
            raise CoordinatorError("missing_prerequisite", missing)

        version = _run([self.config.runtime_python, "--version"])
        match = re.search(r"Python (\d+)\.(\d+)", version.stdout + version.stderr)
        if version.returncode != 0 or match is None:
            _fail(
                "missing_prerequisite", "unable to determine coordinator Python version"
            )
        if tuple(map(int, match.groups())) < (3, 11):
            _fail(
                "missing_prerequisite",
                "coordinator Python must be version 3.11 or newer",
            )

        dependencies = _run(
            [self.config.clockwork_python, "-c", "import flask, tomlkit"],
            cwd=self.config.clockwork_repo,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if dependencies.returncode != 0:
            _fail(
                "missing_prerequisite",
                "Clockwork web Python must import flask and tomlkit",
            )
        self._probe_api()
        return {
            "ok": True,
            "category": "ready",
            "activation": "render-only",
            "repositories": snapshots,
            "snowbridge_inventory": (
                ("fixture" if self.config.inventory_file else "live-read-only")
                if self.config.snowbridge_native_smb_enabled
                else "disabled"
            ),
            "snowbridge_native_smb_enabled": self.config.snowbridge_native_smb_enabled,
            "snowbridge_native_smb_disabled_reason": (
                None
                if self.config.snowbridge_native_smb_enabled
                else NATIVE_SMB_DISABLED_REASON
            ),
            "snowbridge_web_backend_8080_enabled": self.config.snowbridge_web_enabled,
        }

    def validate(self) -> dict[str, Any]:
        return self._preflight()

    def _stage_inputs(self, root: Path) -> dict[str, Path]:
        inputs = root / "inputs"
        inputs.mkdir(mode=0o700)
        clockwork = inputs / "clockwork-web.toml"
        _safe_write(
            clockwork,
            "[[jobs]]\n"
            'name = "clockwork-web-macos"\n'
            'description = "Clockwork loopback web UI behind the Air private edge"\n'
            'scope = "user"\n'
            'service_type = "simple"\n'
            f"working_directory = {_toml_string(self.config.clockwork_repo)}\n"
            f"exec_start = {_toml_string('/bin/bash ' + os.fspath(self.config.clockwork_repo / 'scripts/run_clockwork_web_macos.sh'))}\n"
            f"environment_files = [{_toml_string(self.config.clockwork_environment_file)}]\n"
            'restart = "on-failure"\n'
            'restart_sec = "5"\n'
            'standard_output = "journal"\n'
            'standard_error = "journal"\n'
            'service_install_wanted_by = ["default.target"]\n\n'
            "[jobs.environment]\n"
            'CLOCKWORK_WEB_HOST = "127.0.0.1"\n'
            'CLOCKWORK_WEB_PORT = "5001"\n'
            'CLOCKWORK_LAUNCHD_LABEL = "io.github.casonk.clockwork.clockwork-web-macos"\n'
            'CLOCKWORK_WEB_AUTOGENERATE_CRON = "0"\n'
            f"CLOCKWORK_WEB_PYTHON = {_toml_string(self.config.clockwork_python)}\n",
        )

        snowbridge = inputs / "snowbridge-air-smb.toml"
        if self.config.snowbridge_native_smb_enabled:
            _safe_write(
                snowbridge,
                "schema_version = 1\n"
                'platform = "macos"\n'
                'mode = "render-only"\n'
                f"deployment_id = {_toml_string(self.config.deployment_id)}\n\n"
                "[share]\n"
                f"name = {_toml_string(self.config.share_name)}\n"
                f"path = {_toml_string(self.config.share_path)}\n"
                f"expected_accounts = [{_toml_string(self.config.expected_account)}]\n"
                "guest_access = false\n"
                "read_only = false\n"
                "smb3_encryption_required = true\n\n"
                "[wireguard]\n"
                f"interface = {_toml_string(self.config.wireguard_interface)}\n"
                f"host_address = {_toml_string(self.config.air_address)}\n"
                "allowed_client_addresses = ["
                f"{_toml_string(self.config.mini_address)}, {_toml_string(self.config.pro_address)}]\n\n"
                "[safety]\n"
                "refuse_any_guest_share = true\n"
                "refuse_non_target_shares = true\n"
                "refuse_non_wireguard_listener = true\n"
                "require_pf_default_deny = true\n",
            )

        wiring_dir = inputs / "wiring"
        wiring_dir.mkdir(mode=0o700)
        wiring_base = wiring_dir / "services.toml"
        wiring_local = wiring_dir / "services.local.toml"
        _safe_write(
            wiring_base,
            "# Private staged base; entries live in the adjacent local overlay.\n",
        )
        local_text = (
            "[macos_private_edge]\n"
            f"wireguard_interface = {_toml_string(self.config.wireguard_interface)}\n"
            f"wireguard_address = {_toml_string(self.config.air_address)}\n\n"
            "[[services]]\n"
            'name = "clockwork-air"\n'
            f"hostname = {_toml_string(self.config.air_address.split('/', 1)[0])}\n"
            f"owner_repo = {_toml_string(self.config.clockwork_repo)}\n"
            'access_mode = "shared-mtls"\n'
            'ingress = "wiring-harness-caddy"\n'
            "port = 5001\n"
            'macos_edge_role = "clockwork"\n'
            "macos_edge_listen_port = 8443\n"
        )
        if self.config.snowbridge_web_enabled:
            local_text += (
                "\n[[services]]\n"
                'name = "snowbridge-air"\n'
                f"hostname = {_toml_string(self.config.air_address.split('/', 1)[0])}\n"
                f"owner_repo = {_toml_string(self.config.snowbridge_repo)}\n"
                'access_mode = "snowbridge-mtls"\n'
                'ingress = "wiring-harness-caddy"\n'
                "port = 8080\n"
                'macos_edge_role = "snowbridge"\n'
                "macos_edge_listen_port = 8444\n"
            )
        _safe_write(wiring_local, local_text)

        staged = {
            "clockwork": clockwork,
            "wiring_base": wiring_base,
            "wiring_local": wiring_local,
        }
        if self.config.snowbridge_native_smb_enabled:
            staged["snowbridge"] = snowbridge
        if self.config.snowbridge_native_smb_enabled and self.config.inventory_file:
            inventory = inputs / "snowbridge-inventory.json"
            _safe_write(inventory, self.config.inventory_file.read_bytes())
            staged["inventory"] = inventory
        return staged

    def _record_command(
        self,
        name: str,
        command: Sequence[str | Path],
        *,
        cwd: Path,
        env: Mapping[str, str],
        logs: Path,
        failure_category: str,
    ) -> None:
        result = _run(command, cwd=cwd, env=env, timeout=60)
        _safe_write(logs / f"{name}.stdout.log", result.stdout)
        _safe_write(logs / f"{name}.stderr.log", result.stderr)
        if result.returncode != 0:
            _fail(
                failure_category,
                f"{name} renderer exited with status {result.returncode}",
            )

    def _assert_snowbridge_output_ignored(self) -> None:
        result = _git(
            self.config.snowbridge_repo, "check-ignore", "-q", self.snowbridge_output
        )
        if result.returncode != 0:
            _fail(
                "unsafe_configuration",
                f"Snowbridge render target is not ignored by Git: {self.snowbridge_output}",
            )

    @staticmethod
    def _validate_artifact(path: Path, description: str) -> None:
        _private_file(path, description, missing_is_prerequisite=False)

    def render(self) -> dict[str, Any]:
        preflight = self._preflight()
        root = self.generation_dir
        if root.exists() or root.is_symlink():
            _fail(
                "unsafe_configuration",
                f"generation already exists; refusing overwrite: {root}",
            )
        if self.config.snowbridge_native_smb_enabled:
            if self.snowbridge_output.exists() or self.snowbridge_output.is_symlink():
                _fail(
                    "unsafe_configuration",
                    f"Snowbridge generation already exists; refusing overwrite: {self.snowbridge_output}",
                )
            self._assert_snowbridge_output_ignored()
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
        try:
            _safe_write(
                root / "preflight.json",
                json.dumps(preflight, indent=2, sort_keys=True) + "\n",
            )
            staged = self._stage_inputs(root)
            if (
                preflight["repositories"]
                and preflight["repositories"] != self._validate_repositories()
            ):
                _fail("api_mismatch", "sibling renderer state changed after preflight")
            logs = root / "logs"
            logs.mkdir(mode=0o700)
            outputs = root / "outputs"
            outputs.mkdir(mode=0o700)
            clockwork_output = outputs / "clockwork"
            clockwork_output.mkdir(mode=0o700)
            wiring_output = outputs / "wiring"
            runtime_home = root / "runtime-home"
            runtime_home.mkdir(mode=0o700)

            clockwork_env = {
                **os.environ,
                "HOME": os.fspath(runtime_home),
                "PYTHONPATH": os.fspath(self.config.clockwork_repo / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            self._record_command(
                "clockwork",
                [
                    self.config.runtime_python,
                    "-m",
                    "clockwork",
                    "install",
                    "--manifest",
                    staged["clockwork"],
                    "--target",
                    "launchd-user",
                    "--unit-dir",
                    clockwork_output,
                ],
                cwd=self.config.clockwork_repo,
                env=clockwork_env,
                logs=logs,
                failure_category="render_failure",
            )

            if self.config.snowbridge_native_smb_enabled:
                snow_command: list[str | Path] = [
                    self.config.runtime_python,
                    self.config.snowbridge_repo / "scripts/macos_smb_plan.py",
                    "--config",
                    staged["snowbridge"],
                    "render",
                ]
                if "inventory" in staged:
                    snow_command.extend(["--inventory-file", staged["inventory"]])
                snow_command.extend(["--output", self.snowbridge_output])
                self._record_command(
                    "snowbridge",
                    snow_command,
                    cwd=self.config.snowbridge_repo,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    logs=logs,
                    failure_category="unsafe_configuration",
                )

            self._record_command(
                "wiring-harness",
                [
                    self.config.runtime_python,
                    self.config.wiring_repo / "scripts/render_macos_private_edge.py",
                    "--services",
                    staged["wiring_base"],
                    "--certs-dir",
                    self.config.certs_dir,
                    "--output-dir",
                    wiring_output,
                    "--caddy-binary",
                    self.config.caddy_binary,
                    "--validate-caddy",
                ],
                cwd=self.config.wiring_repo,
                env={
                    **os.environ,
                    "PYTHONPATH": os.fspath(self.config.wiring_repo / "scripts"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                logs=logs,
                failure_category="unsafe_configuration",
            )

            plists = list(clockwork_output.glob("*.plist"))
            if len(plists) != 1:
                _fail(
                    "api_mismatch",
                    "Clockwork must render exactly one LaunchAgent plist",
                )
            expected_artifacts = [plists[0]]
            if self.config.snowbridge_native_smb_enabled:
                expected_artifacts.extend(
                    [
                        self.snowbridge_output / "activation-plan.json",
                        self.snowbridge_output / "snowbridge-smb.pf",
                    ]
                )
            expected_artifacts.extend(
                [
                    wiring_output / "Caddyfile",
                    wiring_output / "manifest.json",
                    wiring_output / "dev.user.wiring-harness.macos-private-edge.plist",
                ]
            )
            for artifact in expected_artifacts:
                self._validate_artifact(artifact, "rendered artifact")
            try:
                clockwork_plist = plistlib.loads(plists[0].read_bytes())
            except (OSError, ValueError) as error:
                raise CoordinatorError(
                    "api_mismatch", f"Clockwork rendered an invalid plist: {error}"
                ) from error
            clockwork_environment = clockwork_plist.get("EnvironmentVariables", {})
            if clockwork_plist.get("WorkingDirectory") != os.fspath(
                self.config.clockwork_repo
            ):
                _fail(
                    "api_mismatch",
                    "Clockwork plist has an unexpected working directory",
                )
            if clockwork_environment.get("CLOCKWORK_WEB_HOST") != "127.0.0.1":
                _fail("api_mismatch", "Clockwork plist must retain the loopback host")
            if clockwork_environment.get("CLOCKWORK_WEB_PORT") != "5001":
                _fail("api_mismatch", "Clockwork plist must retain backend port 5001")
            wiring_manifest = json.loads((wiring_output / "manifest.json").read_text())
            if self.config.snowbridge_native_smb_enabled:
                snow_plan = json.loads(
                    (self.snowbridge_output / "activation-plan.json").read_text()
                )
                if snow_plan.get("activation_supported") is not False:
                    _fail(
                        "api_mismatch",
                        "Snowbridge plan must keep activation unsupported",
                    )
                snow_boundary = snow_plan.get("wireguard_boundary", {})
                if snow_boundary.get("interface") != self.config.wireguard_interface:
                    _fail("api_mismatch", "Snowbridge plan reports the wrong interface")
                if snow_boundary.get("host_address") != self.config.air_address:
                    _fail("api_mismatch", "Snowbridge plan reports the wrong Air /32")
                if snow_boundary.get("allowed_client_addresses") != [
                    self.config.mini_address,
                    self.config.pro_address,
                ]:
                    _fail(
                        "api_mismatch",
                        "Snowbridge plan reports the wrong client /32 set",
                    )
            if wiring_manifest.get("activation") != "render-only":
                _fail("api_mismatch", "wiring-harness manifest must remain render-only")
            if (
                wiring_manifest.get("wireguard_interface")
                != self.config.wireguard_interface
            ):
                _fail(
                    "api_mismatch",
                    "wiring-harness manifest reports the wrong interface",
                )
            if wiring_manifest.get("wireguard_bind") != self.config.air_address:
                _fail(
                    "api_mismatch",
                    "wiring-harness manifest reports the wrong WireGuard /32",
                )
            if wiring_manifest.get("caddy", {}).get("validated") is not True:
                _fail("api_mismatch", "wiring-harness must validate the Caddyfile")
            expected_roles = ["clockwork"] + (
                ["snowbridge"] if self.config.snowbridge_web_enabled else []
            )
            roles = [
                service.get("role") for service in wiring_manifest.get("services", [])
            ]
            if roles != expected_roles:
                _fail(
                    "api_mismatch",
                    "wiring-harness rendered an unexpected macOS edge role set",
                )
            if (
                preflight["repositories"]
                and preflight["repositories"] != self._validate_repositories()
            ):
                _fail("api_mismatch", "sibling renderer state changed during rendering")

            manifest = {
                "schema_version": 1,
                "deployment_id": self.config.deployment_id,
                "generation": self.config.generation,
                "activation": "render-only",
                "activation_supported": False,
                "network": {
                    "wireguard_interface": self.config.wireguard_interface,
                    "air_address": self.config.air_address,
                    "allowed_clients": [
                        self.config.mini_address,
                        self.config.pro_address,
                    ],
                },
                "repositories": preflight["repositories"],
                "snowbridge_native_smb_enabled": self.config.snowbridge_native_smb_enabled,
                "snowbridge_native_smb_disabled_reason": (
                    None
                    if self.config.snowbridge_native_smb_enabled
                    else NATIVE_SMB_DISABLED_REASON
                ),
                "snowbridge_web_backend_8080_enabled": self.config.snowbridge_web_enabled,
                "artifacts": {
                    os.fspath(path): _sha256(path) for path in expected_artifacts
                },
                "blocked_live_steps": [
                    "launchctl bootstrap or load",
                    "Caddy process start",
                    "SMB or share-point mutation",
                    "PF rule loading",
                    "certificate or key generation",
                    "WireGuard or network mutation",
                ],
            }
            _safe_write(
                root / "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
            return manifest
        except CoordinatorError as error:
            try:
                _safe_write(
                    root / "failure-report.json",
                    json.dumps(error.report(), indent=2, sort_keys=True) + "\n",
                )
            except (FileExistsError, OSError):
                pass
            raise
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as error:
            wrapped = CoordinatorError(
                "render_failure", f"render orchestration failed: {error}"
            )
            try:
                _safe_write(
                    root / "failure-report.json",
                    json.dumps(wrapped.report(), indent=2, sort_keys=True) + "\n",
                )
            except (FileExistsError, OSError):
                pass
            raise wrapped from error


def initialize(config_path: Path) -> Path:
    if config_path.exists() or config_path.is_symlink():
        _fail(
            "unsafe_configuration",
            f"refusing to overwrite existing local config: {config_path}",
        )
    if not EXAMPLE_CONFIG.is_file():
        _fail("missing_prerequisite", f"tracked example is missing: {EXAMPLE_CONFIG}")
    _assert_private_destination(config_path)
    _safe_write(config_path, EXAMPLE_CONFIG.read_bytes())
    return config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "init", help="Create the ignored owner-only local config without overwriting"
    )
    subparsers.add_parser(
        "validate", help="Validate configuration, prerequisites, and sibling APIs"
    )
    subparsers.add_parser("render", help="Render one immutable, inert generation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "init":
            created = initialize(arguments.config.absolute())
            print(f"created owner-only local config: {created}")
            print("replace every placeholder; no live state changed")
            return 0
        config = load_config(arguments.config, repo_root=REPO_ROOT)
        coordinator = AirPrimaryCoordinator(config)
        report = (
            coordinator.validate()
            if arguments.command == "validate"
            else coordinator.render()
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        print(
            "activation: render-only (no launchd, Caddy, SMB, PF, or network changes made)"
        )
        return 0
    except CoordinatorError as error:
        print(json.dumps(error.report(), indent=2, sort_keys=True), file=sys.stderr)
        return error.exit_code
    except (OSError, subprocess.SubprocessError) as error:
        wrapped = CoordinatorError(
            "render_failure", f"coordinator I/O failure: {error}"
        )
        print(json.dumps(wrapped.report(), indent=2, sort_keys=True), file=sys.stderr)
        return wrapped.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
