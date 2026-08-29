#!/usr/bin/env python3
"""Render a privacy-safe local rollup of repository ``BACKLOG.md`` files.

The registry and catalog remain the authority for repository identity and local
checkout location.  Each repository's tracked ``BACKLOG.md`` remains the
authority for the work-item text.  This renderer deliberately does not copy
that text: it derives an opaque item ID, state, priority, and generic blocker
class instead.  An ignored, owner-reviewed title map may supply safe summaries
for items that should be recognizable in the local portfolio view.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import portfolio_materializer as materializer
import repository_visibility as visibility


SCHEMA_VERSION = 1
_ITEM_RE = re.compile(r"^\s*-\s*\[(?P<checked>[ xX])\]\s+(?P<text>.+?)\s*$")
_SECTION_RE = re.compile(r"^##\s+(?P<section>.+?)\s*$")
_PRIORITY_RE = re.compile(r"\bP(?P<priority>[0-3])\b", re.IGNORECASE)
_EXTERNAL_RE = re.compile(r"\b(await|blocked|external|manual|need|require|until|wait)\w*\b", re.I)


class BacklogIndexError(Exception):
    """Raised when a local backlog rollup cannot be trusted."""


@dataclass(frozen=True)
class BacklogItem:
    repository_id: str
    repository: str
    item_id: str
    title: str
    priority: str
    state: str
    blocker: str


def _safe_repository_name(pair: visibility.RegistryPair, repository_id: str) -> str:
    for registry_visibility, entry in pair.entries:
        if entry.repository_id == repository_id:
            return entry.slug if registry_visibility == "public" else repository_id
    raise BacklogIndexError("catalog referenced an unknown repository")


def _item_id(repository_id: str, text: str) -> str:
    digest = hashlib.sha256(f"{repository_id}\0{text}".encode("utf-8")).hexdigest()
    return f"item-{digest[:12]}"


def _state(checked: str, section: str, text: str) -> str:
    normalized_section = section.casefold()
    normalized_text = text.casefold()
    if checked.casefold() == "x" or "done" in normalized_section:
        return "done"
    if "in progress" in normalized_section:
        return "in-progress"
    if "blocked" in normalized_text:
        return "blocked"
    return "pending"


def _blocker(text: str, state: str) -> str:
    if state == "blocked":
        return "blocked; see canonical item"
    if _EXTERNAL_RE.search(text):
        return "operator or external prerequisite"
    return "—"


def _parse_backlog(
    path: Path,
    *,
    repository_id: str,
    repository: str,
    titles: dict[str, str],
) -> list[BacklogItem]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BacklogIndexError(f"cannot read canonical backlog: {path}") from exc

    items: list[BacklogItem] = []
    section = "Pending"
    for line in source.splitlines():
        section_match = _SECTION_RE.match(line)
        if section_match:
            section = section_match.group("section")
            continue
        item_match = _ITEM_RE.match(line)
        if item_match is None:
            continue
        text = item_match.group("text")
        item_id = _item_id(repository_id, text)
        priority_match = _PRIORITY_RE.search(text)
        item_state = _state(item_match.group("checked"), section, text)
        items.append(
            BacklogItem(
                repository_id=repository_id,
                repository=repository,
                item_id=item_id,
                title=titles.get(item_id, "Canonical backlog item"),
                priority=(f"P{priority_match.group('priority')}" if priority_match else "unprioritized"),
                state=item_state,
                blocker=_blocker(text, item_state),
            )
        )
    return items


def _load_titles(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        raw = visibility._read_secure_regular_file(path)
        visibility._require_ignored_or_outside_git(path)
        document = json.loads(raw, object_pairs_hook=visibility._object_without_duplicate_keys)
    except (OSError, visibility.RegistryError, json.JSONDecodeError) as exc:
        raise BacklogIndexError(f"cannot load title map: {path}") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "titles"}:
        raise BacklogIndexError("title map must contain exactly schema_version and titles")
    if document["schema_version"] != SCHEMA_VERSION or not isinstance(document["titles"], dict):
        raise BacklogIndexError("title map must use schema_version 1 and a titles object")
    titles: dict[str, str] = {}
    for item_id, title in document["titles"].items():
        if not isinstance(item_id, str) or not re.fullmatch(r"item-[0-9a-f]{12}", item_id):
            raise BacklogIndexError("title map contains an invalid item ID")
        if not isinstance(title, str) or not title.strip() or title != title.strip():
            raise BacklogIndexError("title map contains an invalid title")
        titles[item_id] = title
    return titles


def collect_items(
    pair: visibility.RegistryPair,
    catalog: materializer.CatalogDocument,
    portfolio_root: Path,
    *,
    titles: dict[str, str] | None = None,
) -> tuple[list[BacklogItem], int]:
    """Collect checked Markdown items without exporting canonical item text."""

    selected_titles = titles or {}
    items: list[BacklogItem] = []
    unavailable = 0
    for entry in catalog.repositories:
        if entry.lifecycle != "active" or entry.desired_presence != "checkout":
            continue
        checkout = portfolio_root / entry.relative_path
        backlog = checkout / "BACKLOG.md"
        if not backlog.is_file():
            unavailable += 1
            continue
        items.extend(
            _parse_backlog(
                backlog,
                repository_id=entry.repository_id,
                repository=_safe_repository_name(pair, entry.repository_id),
                titles=selected_titles,
            )
        )
    return sorted(items, key=lambda item: (item.state != "pending", item.priority, item.repository, item.item_id)), unavailable


def render_markdown(items: Iterable[BacklogItem], unavailable: int) -> str:
    rows = list(items)
    lines = [
        "# Portfolio Backlog Index",
        "",
        "Generated locally from registered active checkouts. Canonical item text remains in each repository's `BACKLOG.md`; this view intentionally emits only opaque item IDs and reviewed safe titles.",
        "",
        "| Repository | Item | Safe title | Priority | State | Dependency / blocker |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in rows:
        lines.append(
            f"| `{item.repository}` | `{item.item_id}` | {item.title} | {item.priority} | {item.state} | {item.blocker} |"
        )
    if not rows:
        lines.append("| — | — | No checked backlog items found | — | — | — |")
    lines.extend(
        [
            "",
            f"Unavailable registered active checkouts: {unavailable}.",
            "",
            "To add a recognizable summary, place a reviewed title map in the ignored `config/portfolio/backlog-index.local.json`; start from `config/portfolio/backlog-index.example.json`. Do not copy private names, local paths, credentials, or topology into that map.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_owner_only(path: Path, content: str) -> None:
    try:
        visibility._require_ignored_or_outside_git(path)
    except visibility.RegistryError as exc:
        raise BacklogIndexError(f"output must be ignored or outside Git: {path}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise BacklogIndexError(f"output must be a regular file: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-registry", required=True)
    parser.add_argument("--public-registry", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title-map", help="optional ignored reviewed-title JSON map")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        pair = visibility.load_pair(arguments.private_registry, arguments.public_registry)
        catalog = materializer.load_catalog(arguments.catalog, pair)
        titles = _load_titles(Path(arguments.title_map)) if arguments.title_map else {}
        repo_root = Path(__file__).resolve().parents[1]
        portfolio_root = repo_root.parents[1]
        items, unavailable = collect_items(pair, catalog, portfolio_root, titles=titles)
        _write_owner_only(Path(arguments.output), render_markdown(items, unavailable))
    except (BacklogIndexError, materializer.MaterializerError, visibility.RegistryError) as exc:
        print(f"portfolio backlog index: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
