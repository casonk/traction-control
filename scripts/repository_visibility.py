#!/usr/bin/env python3
"""Validate and audit traction-control's paired repository visibility registry."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 1024 * 1024
ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "generation",
    "visibility",
    "repositories",
}
ENTRY_KEYS = {"id", "slug"}
REGISTRY_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
REPOSITORY_ID_RE = re.compile(r"[A-Za-z0-9_+/=-][A-Za-z0-9_+/=-]{0,127}\Z")
OWNER_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]*\Z")


class RegistryError(Exception):
    """Raised when registry state cannot be trusted."""


class AuditFailure(Exception):
    """Raised when observed repositories differ from a valid registry."""

    def __init__(self, failures: Sequence[str]) -> None:
        super().__init__("repository visibility audit failed")
        self.failures = tuple(failures)


class PrivateDisclosureFailure(Exception):
    """Raised when a Git index discloses a private repository identity."""

    def __init__(self, findings: Sequence["PrivateDisclosureFinding"]) -> None:
        super().__init__("private repository disclosure audit failed")
        self.findings = tuple(findings)


@dataclass(frozen=True)
class RepositoryEntry:
    repository_id: str
    slug: str


@dataclass(frozen=True)
class GitHubRepositoryObservation:
    repository_id: str
    slug: str
    visibility: str


@dataclass(frozen=True)
class PrivateDisclosureFinding:
    root_number: int
    path: str
    count: int


@dataclass(frozen=True)
class RegistryDocument:
    path: Path
    registry_id: str
    generation: int
    visibility: str
    repositories: tuple[RepositoryEntry, ...]


@dataclass(frozen=True)
class RegistryPair:
    private: RegistryDocument
    public: RegistryDocument
    # Repositories that exist only on this machine. They have no GitHub id or
    # slug, so the paired registry -- which is keyed on immutable GitHub ids and
    # reconciled against GitHub -- cannot represent them. Without this they
    # classify as "unclassified", which fails closed to private everywhere that
    # asks, but leaves them invisible to the disclosure matcher: their names
    # could be committed to a tracked file and nothing would object.
    local_private: tuple[str, ...] = ()

    @property
    def registry_id(self) -> str:
        return self.private.registry_id

    @property
    def generation(self) -> int:
        return self.private.generation

    @property
    def entries(self) -> tuple[tuple[str, RepositoryEntry], ...]:
        return tuple(
            [("private", entry) for entry in self.private.repositories]
            + [("public", entry) for entry in self.public.repositories]
        )

    def classification(self, slug: str) -> str:
        slug_key = slug.casefold()
        for visibility, entry in self.entries:
            if entry.slug.casefold() == slug_key:
                return visibility
        name_key = slug.rsplit("/", 1)[-1].casefold()
        if any(name.casefold() == name_key for name in self.local_private):
            return "private"
        return "unclassified"


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise RegistryError(f"{label} has invalid keys ({'; '.join(details)})")


def _validate_slug(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise RegistryError("repository slug must be a non-empty trimmed string")
    if value.count("/") != 1:
        raise RegistryError(f"repository slug must use OWNER/REPO form: {value!r}")
    owner, repository = value.split("/", 1)
    if len(owner) > 39 or OWNER_RE.fullmatch(owner) is None:
        raise RegistryError(f"invalid GitHub owner in repository slug: {value!r}")
    if (
        len(repository) > 100
        or repository in {".", ".."}
        or REPOSITORY_RE.fullmatch(repository) is None
    ):
        raise RegistryError(f"invalid GitHub repository name in slug: {value!r}")
    return value


def _validate_repository_id(value: object) -> str:
    if type(value) is not str or REPOSITORY_ID_RE.fullmatch(value) is None:
        raise RegistryError("repository id must be a valid immutable GitHub node ID")
    return value


def _validate_registry_id(value: object) -> str:
    if type(value) is not str or REGISTRY_ID_RE.fullmatch(value) is None:
        raise RegistryError(
            "registry_id must be a lowercase token containing letters, digits, dot, "
            "underscore, or hyphen"
        )
    return value


def _validate_visibility(value: object, label: str = "visibility") -> str:
    if type(value) is not str or value not in {"private", "public"}:
        raise RegistryError(f"{label} must be exactly 'private' or 'public'")
    return value


def _read_secure_regular_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise RegistryError(f"registry file not found: {path}") from exc
    except OSError as exc:
        raise RegistryError(
            f"registry file must be a readable regular non-symlink file: {path}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RegistryError(f"registry file must be a regular file: {path}")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise RegistryError(f"registry file is not owned by the current user: {path}")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RegistryError(
                f"registry file must not grant group or other access: {path}"
            )
        if metadata.st_size > MAX_CONFIG_BYTES:
            raise RegistryError(f"registry file exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            raw = handle.read(MAX_CONFIG_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise RegistryError(f"registry file exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        return raw
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_ignored_or_outside_git(path: Path) -> None:
    absolute_path = Path(os.path.realpath(path.absolute()))
    parent = absolute_path.parent
    if not parent.is_dir():
        return
    worktree_result = _run(
        ["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
    )
    if worktree_result.returncode != 0:
        return
    worktree = Path(os.path.realpath(worktree_result.stdout.strip()))
    try:
        relative_path = absolute_path.relative_to(worktree)
    except ValueError as exc:
        raise RegistryError(
            f"cannot establish registry path containment in Git worktree: {path}"
        ) from exc
    tracked_result = _run(
        [
            "git",
            "-C",
            str(worktree),
            "ls-files",
            "--error-unmatch",
            "--",
            str(relative_path),
        ]
    )
    if tracked_result.returncode == 0:
        raise RegistryError(f"active registry file must not be tracked by Git: {path}")
    ignored_result = _run(
        [
            "git",
            "-C",
            str(worktree),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            str(relative_path),
        ]
    )
    if ignored_result.returncode != 0:
        raise RegistryError(
            f"active registry file inside a Git worktree must be ignored: {path}"
        )


def _read_document(path_value: str | os.PathLike[str], expected_visibility: str) -> RegistryDocument:
    path = Path(path_value)
    try:
        raw = _read_secure_regular_file(path)
        _require_ignored_or_outside_git(path)
        payload = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except UnicodeError as exc:
        raise RegistryError(f"registry file is not valid UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(
            f"invalid JSON in registry file {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc

    if type(payload) is not dict:
        raise RegistryError(f"registry root must be a JSON object: {path}")
    _require_exact_keys(payload, ROOT_KEYS, f"registry {path}")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise RegistryError(
            f"registry {path} must use schema_version {SCHEMA_VERSION}"
        )
    registry_id = _validate_registry_id(payload["registry_id"])
    generation = payload["generation"]
    if type(generation) is not int or generation < 0:
        raise RegistryError(f"registry generation must be a non-negative integer: {path}")
    if payload["visibility"] != expected_visibility:
        raise RegistryError(
            f"registry {path} visibility must be exactly {expected_visibility!r}"
        )
    raw_repositories = payload["repositories"]
    if type(raw_repositories) is not list:
        raise RegistryError(f"registry repositories must be a JSON array: {path}")

    repositories: list[RepositoryEntry] = []
    ids: set[str] = set()
    slugs: set[str] = set()
    for index, raw_entry in enumerate(raw_repositories):
        if type(raw_entry) is not dict:
            raise RegistryError(f"registry entry {index} must be a JSON object: {path}")
        _require_exact_keys(raw_entry, ENTRY_KEYS, f"registry entry {index} in {path}")
        repository_id = _validate_repository_id(raw_entry["id"])
        slug = _validate_slug(raw_entry["slug"])
        slug_key = slug.casefold()
        if repository_id in ids:
            raise RegistryError(f"duplicate repository id in {path}: {repository_id}")
        if slug_key in slugs:
            raise RegistryError(f"duplicate repository slug in {path}: {slug}")
        ids.add(repository_id)
        slugs.add(slug_key)
        repositories.append(RepositoryEntry(repository_id, slug))

    expected_order = sorted(
        repositories,
        key=lambda entry: (entry.slug.casefold(), entry.repository_id),
    )
    if repositories != expected_order:
        raise RegistryError(f"registry repositories are not deterministically sorted: {path}")

    return RegistryDocument(
        path=path,
        registry_id=registry_id,
        generation=generation,
        visibility=expected_visibility,
        repositories=tuple(repositories),
    )


def _read_local_private(path: str | os.PathLike[str] | None) -> tuple[str, ...]:
    """Names of local-only private repositories, if a registry is supplied.

    Deliberately not reconciled against GitHub: these have no remote, so an
    absent-from-GitHub finding would be the expected state, not a fault.
    """
    if path is None:
        return ()
    document = Path(path)
    if not document.is_file():
        return ()
    try:
        payload = json.loads(document.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"local-only registry is not readable JSON: {document}") from error
    names: list[str] = []
    for entry in payload.get("repositories", []):
        name = (entry or {}).get("name", "").strip() if isinstance(entry, dict) else ""
        if not name:
            raise RegistryError(f"local-only registry entry is missing a name: {document}")
        names.append(name)
    return tuple(names)


def load_pair(
    private_path: str | os.PathLike[str],
    public_path: str | os.PathLike[str],
    local_private_path: str | os.PathLike[str] | None = None,
) -> RegistryPair:
    private = _read_document(private_path, "private")
    public = _read_document(public_path, "public")
    if private.registry_id != public.registry_id:
        raise RegistryError("private and public registry_id values do not match")
    if private.generation != public.generation:
        raise RegistryError("private and public registry generations do not match")

    seen_ids: dict[str, tuple[str, RepositoryEntry]] = {}
    seen_slugs: dict[str, tuple[str, RepositoryEntry]] = {}
    for visibility, document in (("private", private), ("public", public)):
        for entry in document.repositories:
            if entry.repository_id in seen_ids:
                other_visibility, _ = seen_ids[entry.repository_id]
                raise RegistryError(
                    f"repository id appears in both {other_visibility} and "
                    f"{visibility} registries: {entry.repository_id}"
                )
            slug_key = entry.slug.casefold()
            if slug_key in seen_slugs:
                other_visibility, _ = seen_slugs[slug_key]
                raise RegistryError(
                    f"repository slug appears in both {other_visibility} and "
                    f"{visibility} registries: {entry.slug}"
                )
            seen_ids[entry.repository_id] = (visibility, entry)
            seen_slugs[slug_key] = (visibility, entry)
    return RegistryPair(
        private=private,
        public=public,
        local_private=_read_local_private(local_private_path),
    )


def _document_payload(
    *,
    registry_id: str,
    generation: int,
    visibility: str,
    repositories: Iterable[RepositoryEntry],
) -> dict[str, Any]:
    ordered = sorted(
        repositories,
        key=lambda entry: (entry.slug.casefold(), entry.repository_id),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_id": registry_id,
        "generation": generation,
        "visibility": visibility,
        "repositories": [
            {"id": entry.repository_id, "slug": entry.slug} for entry in ordered
        ],
    }


def _serialized(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _write_temp(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp.local.json",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_serialized(payload))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_pair(
    private_path: Path,
    public_path: Path,
    private_payload: dict[str, Any],
    public_payload: dict[str, Any],
) -> None:
    private_temp: Path | None = None
    public_temp: Path | None = None
    try:
        private_temp = _write_temp(private_path, private_payload)
        public_temp = _write_temp(public_path, public_payload)
        os.replace(private_temp, private_path)
        _fsync_directory(private_path.parent)
        os.replace(public_temp, public_path)
        _fsync_directory(public_path.parent)
    finally:
        if private_temp is not None:
            private_temp.unlink(missing_ok=True)
        if public_temp is not None:
            public_temp.unlink(missing_ok=True)


class _RegistryLock:
    def __init__(self, private_path: Path) -> None:
        self.path = private_path.parent / ".repository-visibility.lock"
        self.descriptor: int | None = None

    def __enter__(self) -> "_RegistryLock":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise RegistryError(f"cannot open registry lock: {self.path}") from exc
        try:
            metadata = os.fstat(self.descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RegistryError(f"registry lock is not a regular file: {self.path}")
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise RegistryError(
                    f"registry lock is not owned by the current user: {self.path}"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RegistryError(f"registry lock has unsafe permissions: {self.path}")
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        except BaseException:
            os.close(self.descriptor)
            self.descriptor = None
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


def initialize_pair(private_path_value: str, public_path_value: str, registry_id_value: str) -> None:
    private_path = Path(private_path_value)
    public_path = Path(public_path_value)
    registry_id = _validate_registry_id(registry_id_value)
    if private_path == public_path:
        raise RegistryError("private and public registry paths must differ")
    private_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    public_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_ignored_or_outside_git(private_path)
    _require_ignored_or_outside_git(public_path)
    with _RegistryLock(private_path):
        if private_path.exists() or public_path.exists():
            raise RegistryError("init refuses to overwrite an existing registry file")
        _replace_pair(
            private_path,
            public_path,
            _document_payload(
                registry_id=registry_id,
                generation=0,
                visibility="private",
                repositories=(),
            ),
            _document_payload(
                registry_id=registry_id,
                generation=0,
                visibility="public",
                repositories=(),
            ),
        )
    load_pair(private_path, public_path)


def record_private(
    private_path_value: str,
    public_path_value: str,
    repository_id_value: str,
    slug_value: str,
) -> bool:
    private_path = Path(private_path_value)
    public_path = Path(public_path_value)
    repository_id = _validate_repository_id(repository_id_value)
    slug = _validate_slug(slug_value)
    with _RegistryLock(private_path):
        pair = load_pair(private_path, public_path)
        slug_key = slug.casefold()
        for visibility, entry in pair.entries:
            id_match = entry.repository_id == repository_id
            slug_match = entry.slug.casefold() == slug_key
            if id_match and slug_match:
                if visibility != "private":
                    raise RegistryError(
                        f"repository is already classified public: {entry.slug}"
                    )
                return False
            if id_match:
                raise RegistryError(
                    f"repository id is already bound to a different slug: {entry.slug}"
                )
            if slug_match:
                raise RegistryError(
                    f"repository slug is already bound to a different id: {entry.slug}"
                )

        generation = pair.generation + 1
        private_repositories = list(pair.private.repositories)
        private_repositories.append(RepositoryEntry(repository_id, slug))
        _replace_pair(
            private_path,
            public_path,
            _document_payload(
                registry_id=pair.registry_id,
                generation=generation,
                visibility="private",
                repositories=private_repositories,
            ),
            _document_payload(
                registry_id=pair.registry_id,
                generation=generation,
                visibility="public",
                repositories=pair.public.repositories,
            ),
        )
        load_pair(private_path, public_path)
        return True


def _normalize_github_remote(remote: str) -> str | None:
    remote = remote.strip()
    prefixes = (
        "https://github.com/",
        "ssh://git@github.com/",
    )
    path: str | None = None
    for prefix in prefixes:
        if remote.startswith(prefix):
            path = remote[len(prefix) :]
            break
    if path is None and remote.startswith("git@github.com:"):
        path = remote[len("git@github.com:") :]
    if path is None:
        return None
    path = path.removesuffix(".git").rstrip("/")
    try:
        return _validate_slug(path)
    except RegistryError:
        return None


def _discover_git_repositories(root: Path) -> list[Path]:
    if not root.is_dir():
        raise RegistryError(f"portfolio root is not a directory: {root}")

    def fail_on_walk_error(error: OSError) -> None:
        location = error.filename if error.filename is not None else root
        raise RegistryError(f"cannot scan portfolio tree at {location}: {error}") from error

    repositories: list[Path] = []
    for current, directories, files in os.walk(
        root,
        onerror=fail_on_walk_error,
        followlinks=False,
    ):
        directories[:] = [
            directory
            for directory in directories
            if directory
            not in {
                ".git",
                ".tox",
                ".venv",
                "archive-repos",
                "node_modules",
                "vendor",
            }
        ]
        current_path = Path(current)
        if ".git" in files or (current_path / ".git").is_dir():
            repositories.append(current_path)
            directories[:] = []
    return sorted(repositories, key=lambda path: str(path).casefold())


def _run(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
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
        raise RegistryError(f"cannot run command {command[0]!r}: {exc}") from exc


def _run_bytes(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RegistryError(f"cannot run command {command[0]!r}: {exc}") from exc


def _github_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["GH_HOST"] = "github.com"
    return environment


def _resolve_gh_command(gh_command: str) -> str:
    gh_path = gh_command if os.path.sep in gh_command else shutil.which(gh_command)
    if not gh_path:
        raise RegistryError(f"GitHub CLI not found: {gh_command}")
    return gh_path


def _observe_github_repository(
    gh_command: str,
    slug: str,
) -> GitHubRepositoryObservation:
    gh_path = _resolve_gh_command(gh_command)
    result = _run(
        [
            gh_path,
            "repo",
            "view",
            slug,
            "--json",
            "id,nameWithOwner,visibility",
        ],
        environment=_github_environment(),
    )
    if result.returncode != 0:
        raise RegistryError("GitHub repository observation failed on github.com")
    try:
        observed = json.loads(
            result.stdout,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (json.JSONDecodeError, RegistryError) as exc:
        raise RegistryError(
            "GitHub returned malformed repository identity data from github.com"
        ) from exc
    if type(observed) is not dict:
        raise RegistryError(
            "GitHub returned non-object repository identity data from github.com"
        )
    try:
        _require_exact_keys(
            observed,
            {"id", "nameWithOwner", "visibility"},
            "GitHub repository observation",
        )
        repository_id = _validate_repository_id(observed.get("id"))
        canonical_slug = _validate_slug(observed.get("nameWithOwner"))
    except RegistryError as exc:
        raise RegistryError(
            "GitHub returned invalid repository identity data from github.com"
        ) from exc
    observed_visibility = observed.get("visibility")
    if observed_visibility not in {"PRIVATE", "PUBLIC"}:
        raise RegistryError(
            "GitHub returned an unsupported repository visibility from github.com"
        )
    return GitHubRepositoryObservation(
        repository_id=repository_id,
        slug=canonical_slug,
        visibility=observed_visibility.lower(),
    )


def reconcile_observed(
    private_path_value: str,
    public_path_value: str,
    repository_id_value: str,
    from_slug_value: str,
    from_visibility_value: str,
    to_slug_value: str,
    to_visibility_value: str,
    *,
    gh_command: str = "gh",
) -> bool:
    """Reconcile one manually changed GitHub repository into the local registry."""

    private_path = Path(private_path_value)
    public_path = Path(public_path_value)
    repository_id = _validate_repository_id(repository_id_value)
    from_slug = _validate_slug(from_slug_value)
    from_visibility = _validate_visibility(
        from_visibility_value,
        "from visibility",
    )
    to_slug = _validate_slug(to_slug_value)
    to_visibility = _validate_visibility(to_visibility_value, "to visibility")

    observed = _observe_github_repository(gh_command, to_slug)
    if observed.repository_id != repository_id:
        raise RegistryError(
            "observed GitHub repository ID does not match the requested immutable ID"
        )
    if observed.slug != to_slug:
        raise RegistryError(
            "observed canonical GitHub slug does not exactly match the requested target"
        )
    if observed.visibility != to_visibility:
        raise RegistryError(
            "observed GitHub visibility does not exactly match the requested target"
        )

    with _RegistryLock(private_path):
        pair = load_pair(private_path, public_path)
        current: tuple[str, RepositoryEntry] | None = None
        for visibility, entry in pair.entries:
            if entry.repository_id == repository_id:
                current = (visibility, entry)
                break
        if current is None:
            raise RegistryError(
                "requested immutable repository ID is absent from the registry"
            )

        current_visibility, current_entry = current
        if current_entry.slug == to_slug and current_visibility == to_visibility:
            return False
        if (
            current_entry.slug != from_slug
            or current_visibility != from_visibility
        ):
            raise RegistryError(
                "locked registry entry does not match the requested source state"
            )

        target_slug_key = to_slug.casefold()
        for _, entry in pair.entries:
            if (
                entry.repository_id != repository_id
                and entry.slug.casefold() == target_slug_key
            ):
                raise RegistryError(
                    "requested target slug is bound to a different repository ID"
                )

        private_repositories = [
            entry
            for entry in pair.private.repositories
            if entry.repository_id != repository_id
        ]
        public_repositories = [
            entry
            for entry in pair.public.repositories
            if entry.repository_id != repository_id
        ]
        target_entry = RepositoryEntry(repository_id, to_slug)
        if to_visibility == "private":
            private_repositories.append(target_entry)
        else:
            public_repositories.append(target_entry)

        generation = pair.generation + 1
        _replace_pair(
            private_path,
            public_path,
            _document_payload(
                registry_id=pair.registry_id,
                generation=generation,
                visibility="private",
                repositories=private_repositories,
            ),
            _document_payload(
                registry_id=pair.registry_id,
                generation=generation,
                visibility="public",
                repositories=public_repositories,
            ),
        )
        updated = load_pair(private_path, public_path)
        for visibility, entry in updated.entries:
            if entry.repository_id == repository_id:
                if entry.slug != to_slug or visibility != to_visibility:
                    raise RegistryError(
                        "reconciled registry entry failed post-write verification"
                    )
                return True
        raise RegistryError("reconciled repository ID disappeared after the update")


def _audit_github(
    pair: RegistryPair,
    gh_command: str,
    managed_owners: Sequence[str],
) -> list[str]:
    gh_path = _resolve_gh_command(gh_command)
    failures: list[str] = []
    expected_by_slug = {
        entry.slug.casefold(): (visibility, entry)
        for visibility, entry in pair.entries
    }
    owners = {entry.slug.split("/", 1)[0] for _, entry in pair.entries}
    for owner in managed_owners:
        if OWNER_RE.fullmatch(owner) is None:
            raise RegistryError(f"invalid managed GitHub owner: {owner!r}")
        owners.add(owner)
    if not owners:
        raise RegistryError(
            "remote audit needs at least one registry entry or explicit --owner"
        )

    observed_slugs: set[str] = set()
    for owner in sorted(owners, key=str.casefold):
        result = _run(
            [
                gh_path,
                "repo",
                "list",
                owner,
                "--limit",
                "1000",
                "--json",
                "id,nameWithOwner,visibility",
            ],
            environment=_github_environment(),
        )
        if result.returncode != 0:
            failures.append(f"{owner}: GitHub repository inventory failed")
            continue
        try:
            observed_repositories = json.loads(
                result.stdout,
                object_pairs_hook=_object_without_duplicate_keys,
            )
        except (json.JSONDecodeError, RegistryError):
            failures.append(f"{owner}: GitHub returned malformed repository inventory")
            continue
        if type(observed_repositories) is not list:
            failures.append(f"{owner}: GitHub returned non-array repository inventory")
            continue
        if len(observed_repositories) >= 1000:
            failures.append(
                f"{owner}: repository inventory reached the 1000-item safety limit"
            )
        for observed in observed_repositories:
            if type(observed) is not dict:
                failures.append(f"{owner}: GitHub inventory contains a non-object entry")
                continue
            observed_id = observed.get("id")
            observed_slug = observed.get("nameWithOwner")
            observed_visibility = observed.get("visibility")
            try:
                canonical_slug = _validate_slug(observed_slug)
                canonical_id = _validate_repository_id(observed_id)
            except RegistryError:
                failures.append(f"{owner}: GitHub inventory contains invalid identity data")
                continue
            slug_key = canonical_slug.casefold()
            observed_slugs.add(slug_key)
            expected = expected_by_slug.get(slug_key)
            if expected is None:
                failures.append(
                    f"{canonical_slug}: remote repository is absent from the registry"
                )
                continue
            expected_visibility, entry = expected
            if canonical_id != entry.repository_id:
                failures.append(
                    f"{entry.slug}: immutable GitHub repository ID changed"
                )
            if canonical_slug != entry.slug:
                failures.append(
                    f"{entry.slug}: canonical GitHub slug is now {canonical_slug!r}"
                )
            if observed_visibility != expected_visibility.upper():
                failures.append(
                    f"{entry.slug}: expected {expected_visibility}, observed "
                    f"{observed_visibility!r}"
                )
    for _, entry in pair.entries:
        if entry.slug.casefold() not in observed_slugs:
            failures.append(f"{entry.slug}: registered repository is absent from GitHub inventory")
    return failures


def _audit_local_roots(pair: RegistryPair, roots: Sequence[str]) -> list[str]:
    classified = {
        entry.slug.casefold(): (visibility, entry)
        for visibility, entry in pair.entries
    }
    failures: list[str] = []
    for root_value in roots:
        root = Path(root_value).resolve()
        for repository in _discover_git_repositories(root):
            remotes_result = _run(["git", "-C", str(repository), "remote"])
            remotes = tuple(
                remote.strip()
                for remote in remotes_result.stdout.splitlines()
                if remote.strip()
            )
            if remotes_result.returncode != 0 or not remotes:
                failures.append(f"{repository}: local repository has no configured remotes")
                continue
            audited_urls: set[str] = set()
            repository_identities: dict[str, tuple[str, RepositoryEntry]] = {}
            for remote in remotes:
                for mode_arguments, mode_label in (
                    (("--all",), "fetch"),
                    (("--push", "--all"), "push"),
                ):
                    urls_result = _run(
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
                    if urls_result.returncode != 0:
                        failures.append(
                            f"{repository}: cannot resolve {mode_label} URLs for "
                            f"remote {remote}"
                        )
                        continue
                    for remote_url in urls_result.stdout.splitlines():
                        remote_url = remote_url.strip()
                        if not remote_url or remote_url in audited_urls:
                            continue
                        audited_urls.add(remote_url)
                        slug = _normalize_github_remote(remote_url)
                        if slug is None:
                            failures.append(
                                f"{repository}: {mode_label} URL for remote {remote} "
                                "is not a canonical GitHub repository"
                            )
                            continue
                        classified_identity = classified.get(slug.casefold())
                        if classified_identity is None:
                            failures.append(
                                f"{repository}: unclassified {mode_label} destination {slug}"
                            )
                            continue
                        visibility, entry = classified_identity
                        repository_identities[entry.repository_id] = (
                            visibility,
                            entry,
                        )
            if len(repository_identities) > 1:
                destinations = ", ".join(
                    f"{entry.slug} ({visibility})"
                    for visibility, entry in sorted(
                        repository_identities.values(),
                        key=lambda item: item[1].slug.casefold(),
                    )
                )
                failures.append(
                    f"{repository}: local repository has remotes for multiple "
                    f"registered identities: {destinations}"
                )
    return failures


def audit_pair(
    pair: RegistryPair,
    *,
    gh_command: str,
    portfolio_roots: Sequence[str],
    skip_github: bool,
    managed_owners: Sequence[str] = (),
) -> None:
    failures: list[str] = []
    if not skip_github:
        failures.extend(_audit_github(pair, gh_command, managed_owners))
    failures.extend(_audit_local_roots(pair, portfolio_roots))
    if failures:
        raise AuditFailure(failures)


def _private_disclosure_matcher(
    pair: RegistryPair,
) -> tuple[re.Pattern[bytes] | None, int, re.Pattern[str] | None]:
    full_slugs = {
        entry.slug.casefold(): entry.slug.encode("ascii")
        for entry in pair.private.repositories
    }
    repository_names = {
        entry.slug.rsplit("/", 1)[1].casefold(): entry.slug.rsplit("/", 1)[1].encode(
            "ascii"
        )
        for entry in pair.private.repositories
    }
    for name in pair.local_private:
        repository_names.setdefault(name.casefold(), name.encode("ascii"))
    if not full_slugs and not repository_names:
        return None, 0, None

    alternatives: list[bytes] = [
        re.escape(value) + rb"(?:\.git)?"
        for value in full_slugs.values()
    ]
    alternatives.extend(re.escape(value) for value in repository_names.values())
    alternatives.sort(key=len, reverse=True)
    token_characters = rb"A-Za-z0-9._-"
    matcher = re.compile(
        rb"(?<![" + token_characters + rb"])(?:"
        + rb"|".join(alternatives)
        + rb")(?!["
        + token_characters
        + rb"])",
        re.IGNORECASE,
    )
    maximum_match_bytes = max(
        [len(value) + len(b".git") for value in full_slugs.values()]
        + [len(value) for value in repository_names.values()]
    )

    redaction_values = [
        entry.slug for entry in pair.private.repositories
    ] + [
        entry.slug.rsplit("/", 1)[1] for entry in pair.private.repositories
    ]
    redaction_values = sorted(
        set(redaction_values),
        key=lambda value: (-len(value), value.casefold()),
    )
    redactor = re.compile(
        "|".join(re.escape(value) for value in redaction_values),
        re.IGNORECASE,
    )
    return matcher, maximum_match_bytes, redactor


def _private_path_disclosure_matcher(
    pair: RegistryPair,
) -> re.Pattern[bytes] | None:
    full_slugs = {
        entry.slug.casefold(): entry.slug.encode("ascii")
        for entry in pair.private.repositories
    }
    repository_names = {
        entry.slug.rsplit("/", 1)[1].casefold(): entry.slug.rsplit("/", 1)[1].encode(
            "ascii"
        )
        for entry in pair.private.repositories
    }
    if not full_slugs:
        return None
    alternatives = [
        re.escape(value)
        for value in tuple(full_slugs.values()) + tuple(repository_names.values())
    ]
    alternatives.sort(key=len, reverse=True)
    return re.compile(
        rb"(?<![A-Za-z0-9._-])(?:"
        + rb"|".join(alternatives)
        + rb")(?=(?:\.[A-Za-z0-9][A-Za-z0-9._-]*)?(?:/|$))",
        re.IGNORECASE,
    )


def _resolve_disclosure_root(root_value: str, root_number: int) -> Path:
    label = f"root[{root_number}]"
    try:
        root = Path(root_value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RegistryError(f"{label} cannot be resolved") from exc
    if not root.is_dir():
        raise RegistryError(f"{label} is not a directory")
    result = _run(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
    if result.returncode != 0 or not result.stdout.strip():
        raise RegistryError(f"{label} is not a Git worktree")
    try:
        top_level = Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RegistryError(f"{label} Git top-level path cannot be resolved") from exc
    if top_level != root:
        raise RegistryError(f"{label} must be the Git worktree top level")
    return root


def _git_index_snapshot(
    root: Path,
    root_number: int,
) -> tuple[dict[bytes, list[bytes]], list[bytes]]:
    label = f"root[{root_number}]"
    result = _run_bytes(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(root),
            "ls-files",
            "--stage",
            "-z",
        ]
    )
    if result.returncode != 0:
        raise RegistryError(f"{label} Git index cannot be read")

    blobs: dict[bytes, list[bytes]] = {}
    paths: list[bytes] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.split(b" ")
        except ValueError as exc:
            raise RegistryError(f"{label} Git index returned malformed metadata") from exc
        if not path:
            raise RegistryError(f"{label} Git index contains an empty path")
        if stage != b"0":
            raise RegistryError(f"{label} Git index contains unresolved entries")
        if mode not in {b"100644", b"100755", b"120000", b"160000"}:
            raise RegistryError(f"{label} Git index contains an unsupported entry mode")
        if re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", object_id) is None:
            raise RegistryError(f"{label} Git index contains an invalid object ID")
        paths.append(path)
        if mode == b"160000":
            continue
        blobs.setdefault(object_id, []).append(path)
    return blobs, paths


def _count_stream_matches(
    stream: BinaryIO,
    size: int,
    matcher: re.Pattern[bytes],
    maximum_match_bytes: int,
) -> int:
    remaining = size
    carry = b""
    total_read = 0
    processed_until = 0
    count = 0
    deferred_bytes = maximum_match_bytes + 1
    carry_bytes = deferred_bytes + 1
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise RegistryError("Git index blob content could not be read")
        remaining -= len(chunk)
        total_read += len(chunk)
        data = carry + chunk
        base_offset = total_read - len(data)
        safe_limit = (
            total_read
            if remaining == 0
            else max(processed_until, total_read - deferred_bytes)
        )
        for match in matcher.finditer(data):
            absolute_start = base_offset + match.start()
            if processed_until <= absolute_start < safe_limit:
                count += 1
        processed_until = safe_limit
        if remaining:
            carry = data[-carry_bytes:]
    return count


def _scan_git_index_blobs(
    root: Path,
    root_number: int,
    blobs: Mapping[bytes, Sequence[bytes]],
    matcher: re.Pattern[bytes],
    maximum_match_bytes: int,
) -> dict[bytes, int]:
    label = f"root[{root_number}]"
    if not blobs:
        return {}

    process: subprocess.Popen[bytes] | None = None
    try:
        with tempfile.TemporaryFile(mode="w+b") as requests:
            for object_id in blobs:
                requests.write(object_id + b"\n")
            requests.seek(0)
            environment = dict(os.environ)
            environment["GIT_NO_LAZY_FETCH"] = "1"
            environment["GIT_NO_REPLACE_OBJECTS"] = "1"
            try:
                process = subprocess.Popen(
                    [
                        "git",
                        "--no-optional-locks",
                        "-c",
                        "core.fsmonitor=false",
                        "-C",
                        str(root),
                        "cat-file",
                        "--batch",
                    ],
                    stdin=requests,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                )
            except OSError as exc:
                raise RegistryError(f"{label} Git index blobs cannot be opened") from exc
            if process.stdout is None:
                raise RegistryError(f"{label} Git index blob reader did not start")

            counts: dict[bytes, int] = {}
            for expected_object_id in blobs:
                header = process.stdout.readline()
                fields = header.rstrip(b"\n").split(b" ")
                if (
                    len(fields) != 3
                    or fields[0] != expected_object_id
                    or fields[1] != b"blob"
                    or not fields[2].isdigit()
                ):
                    raise RegistryError(f"{label} Git index blob metadata is unreadable")
                size = int(fields[2])
                counts[expected_object_id] = _count_stream_matches(
                    process.stdout,
                    size,
                    matcher,
                    maximum_match_bytes,
                )
                if process.stdout.read(1) != b"\n":
                    raise RegistryError(f"{label} Git index blob framing is invalid")

            if process.stdout.read(1) != b"":
                raise RegistryError(f"{label} Git index blob reader returned extra data")
            try:
                return_code = process.wait(timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise RegistryError(f"{label} Git index blob reader did not exit") from exc
            if return_code != 0:
                raise RegistryError(f"{label} Git index blob reader failed")
            return counts
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()


def audit_private_disclosures(
    pair: RegistryPair,
    roots: Sequence[str],
) -> None:
    """Fail when supplied Git index paths or blobs disclose a private identity."""

    if not roots:
        raise RegistryError("private disclosure audit requires at least one root")
    matcher, maximum_match_bytes, redactor = _private_disclosure_matcher(pair)
    path_matcher = _private_path_disclosure_matcher(pair)
    findings: list[PrivateDisclosureFinding] = []
    for root_number, root_value in enumerate(roots, start=1):
        root = _resolve_disclosure_root(root_value, root_number)
        blobs, index_paths = _git_index_snapshot(root, root_number)
        if matcher is None:
            continue
        counts = _scan_git_index_blobs(
            root,
            root_number,
            blobs,
            matcher,
            maximum_match_bytes,
        )
        path_counts: dict[bytes, int] = {
            raw_path: (
                sum(1 for _ in path_matcher.finditer(raw_path))
                if path_matcher is not None
                else 0
            )
            for raw_path in index_paths
        }
        for object_id, paths in blobs.items():
            count = counts.get(object_id, 0)
            if count == 0:
                continue
            for raw_path in paths:
                path_counts[raw_path] = path_counts.get(raw_path, 0) + count
        for raw_path, count in path_counts.items():
            if count == 0:
                continue
            rendered_path = os.fsdecode(raw_path)
            if redactor is not None:
                rendered_path = redactor.sub("<private>", rendered_path)
            findings.append(
                PrivateDisclosureFinding(
                    root_number=root_number,
                    path=rendered_path,
                    count=count,
                )
            )
    if findings:
        findings.sort(
            key=lambda finding: (
                finding.root_number,
                finding.path.casefold(),
                finding.path,
            )
        )
        raise PrivateDisclosureFailure(findings)


def _add_pair_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--private", required=True, help="Private registry JSON path.")
    parser.add_argument("--public", required=True, help="Public registry JSON path.")
    parser.add_argument(
        "--local-private",
        default=None,
        help=(
            "Optional registry of local-only private repositories. These have no "
            "GitHub remote, so they cannot appear in the paired registry, but their "
            "names must still be treated as private."
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage traction-control's fail-closed repository visibility registry."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create an empty paired registry.")
    _add_pair_arguments(init_parser)
    init_parser.add_argument("--registry-id", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a paired registry.")
    _add_pair_arguments(validate_parser)
    validate_parser.add_argument("--quiet", action="store_true")

    classify_parser = subparsers.add_parser("classify", help="Classify one GitHub slug.")
    _add_pair_arguments(classify_parser)
    classify_parser.add_argument("--slug", required=True)

    record_parser = subparsers.add_parser(
        "record-private",
        help="Record a verified private GitHub repository.",
    )
    _add_pair_arguments(record_parser)
    record_parser.add_argument("--id", required=True, dest="repository_id")
    record_parser.add_argument("--slug", required=True)

    reconcile_parser = subparsers.add_parser(
        "reconcile-observed",
        help=(
            "Record one already-manual GitHub rename or visibility change "
            "after observing github.com."
        ),
    )
    _add_pair_arguments(reconcile_parser)
    reconcile_parser.add_argument("--id", required=True, dest="repository_id")
    reconcile_parser.add_argument("--from-slug", required=True)
    reconcile_parser.add_argument(
        "--from-visibility",
        required=True,
        choices=("private", "public"),
    )
    reconcile_parser.add_argument("--to-slug", required=True)
    reconcile_parser.add_argument(
        "--to-visibility",
        required=True,
        choices=("private", "public"),
    )
    reconcile_parser.add_argument(
        "--gh",
        default="gh",
        help="GitHub CLI command or path.",
    )

    audit_parser = subparsers.add_parser(
        "audit",
        help="Compare the registry with GitHub and optional local portfolio roots.",
    )
    _add_pair_arguments(audit_parser)
    audit_parser.add_argument("--gh", default="gh", help="GitHub CLI command or path.")
    audit_parser.add_argument(
        "--owner",
        action="append",
        default=[],
        help="Managed GitHub owner to inventory; inferred from entries by default.",
    )
    audit_parser.add_argument(
        "--portfolio-root",
        action="append",
        default=[],
        help="Local portfolio root to scan; repeat as needed.",
    )
    audit_parser.add_argument(
        "--skip-github",
        action="store_true",
        help="Skip remote checks; this does not prove hosted visibility.",
    )

    disclosure_parser = subparsers.add_parser(
        "audit-private-disclosures",
        help="Scan Git index paths and blobs for private repository references.",
    )
    _add_pair_arguments(disclosure_parser)
    disclosure_parser.add_argument(
        "--root",
        action="append",
        required=True,
        help="Git worktree root whose index to scan; repeat as needed.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "init":
            initialize_pair(args.private, args.public, args.registry_id)
            print(f"initialized registry {args.registry_id} generation 0")
            return 0

        if args.command == "reconcile-observed":
            changed = reconcile_observed(
                args.private,
                args.public,
                args.repository_id,
                args.from_slug,
                args.from_visibility,
                args.to_slug,
                args.to_visibility,
                gh_command=args.gh,
            )
            print("reconciled observed repository" if changed else "already reconciled")
            return 0

        pair = load_pair(args.private, args.public, getattr(args, 'local_private', None))
        if args.command == "validate":
            if not args.quiet:
                print(
                    f"valid registry {pair.registry_id} generation {pair.generation}: "
                    f"{len(pair.private.repositories)} private, "
                    f"{len(pair.public.repositories)} public"
                )
            return 0
        if args.command == "classify":
            slug = _validate_slug(args.slug)
            print(pair.classification(slug))
            return 0
        if args.command == "record-private":
            added = record_private(
                args.private,
                args.public,
                args.repository_id,
                args.slug,
            )
            print("recorded private" if added else "already recorded private")
            return 0
        if args.command == "audit":
            audit_pair(
                pair,
                gh_command=args.gh,
                portfolio_roots=args.portfolio_root,
                skip_github=args.skip_github,
                managed_owners=args.owner,
            )
            print(
                f"audit passed for registry {pair.registry_id} generation {pair.generation}"
            )
            return 0
        if args.command == "audit-private-disclosures":
            audit_private_disclosures(pair, args.root)
            print(
                f"private disclosure audit passed for {len(args.root)} Git root(s)"
            )
            return 0
        raise RegistryError(f"unsupported command: {args.command}")
    except PrivateDisclosureFailure as exc:
        for finding in exc.findings:
            rendered_path = json.dumps(finding.path, ensure_ascii=True)
            print(
                f"error: root[{finding.root_number}] file={rendered_path} "
                f"count={finding.count}",
                file=sys.stderr,
            )
        return 1
    except AuditFailure as exc:
        for failure in exc.failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
