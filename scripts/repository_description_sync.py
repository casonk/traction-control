#!/usr/bin/env python3
"""Sync missing GitHub descriptions from each registered repo's merged settings."""

from __future__ import annotations

import argparse
import base64
import binascii
import sys
from pathlib import Path

import repo_settings
import repository_visibility


REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_REGISTRY = REPO_ROOT / "config/repository-visibility/private.local.json"
PUBLIC_REGISTRY = REPO_ROOT / "config/repository-visibility/public.local.json"


def read_remote_settings(slug: str, default_branch: str) -> repo_settings.Settings | None:
    try:
        content = repo_settings.gh_api(
            f"repos/{slug}/contents/{repo_settings.SETTINGS_PATH}",
            "-f",
            f"ref={default_branch}",
        )
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise

    if not isinstance(content, dict) or content.get("encoding") != "base64":
        raise RuntimeError(f"unexpected contents response for {repo_settings.SETTINGS_PATH}")
    encoded = content.get("content")
    if not isinstance(encoded, str):
        raise RuntimeError(f"missing content for {repo_settings.SETTINGS_PATH}")
    try:
        text = base64.b64decode("".join(encoded.split()), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeError(f"invalid encoded contents for {repo_settings.SETTINGS_PATH}") from exc
    return repo_settings.parse_settings(text)


def sync_pair(pair: repository_visibility.RegistryPair, *, dry_run: bool = False) -> int:
    entries = [
        (visibility, entry)
        for visibility, document in (
            ("private", pair.private),
            ("public", pair.public),
        )
        for entry in document.repositories
    ]
    applied = 0
    missing = 0
    failed = 0

    for visibility, entry in sorted(entries, key=lambda item: item[1].slug.casefold()):
        label = entry.slug if visibility == "public" else "private repository"
        try:
            owner, name = entry.slug.split("/", 1)
            remote = repo_settings.fetch_remote(owner, name)
        except (RuntimeError, ValueError) as exc:
            detail = str(exc).replace(entry.slug, label)
            print(f"ERROR {label}: {detail}", file=sys.stderr)
            failed += 1
            continue

        if remote.description:
            continue

        missing += 1
        if remote.archived:
            print(f"SKIP {label}: archived repository has no GitHub description")
            failed += 1
            continue

        try:
            settings = read_remote_settings(entry.slug, remote.default_branch)
            if settings is None:
                print(f"ERROR {label}: {repo_settings.SETTINGS_PATH} is missing")
                failed += 1
                continue
            action = "PLAN" if dry_run else "APPLY"
            print(f"{action} {label}: description")
            if not dry_run:
                repo_settings.gh_api(
                    "-X",
                    "PATCH",
                    f"repos/{entry.slug}",
                    "-f",
                    f"description={settings.description}",
                )
                applied += 1
        except (RuntimeError, repo_settings.SettingsError) as exc:
            detail = str(exc).replace(entry.slug, label)
            print(f"ERROR {label}: {detail}", file=sys.stderr)
            failed += 1

    print(
        f"Description sync: {len(entries)} registered; {missing} missing; "
        f"{applied} applied; {failed} require review."
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, default=PRIVATE_REGISTRY)
    parser.add_argument("--public", type=Path, default=PUBLIC_REGISTRY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        pair = repository_visibility.load_pair(args.private, args.public)
    except repository_visibility.RegistryError as exc:
        parser.error(str(exc))
    return sync_pair(pair, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
