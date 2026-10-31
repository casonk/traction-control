#!/usr/bin/env python3
"""Enroll repositories' web pages in the portfolio theme and top bar, whatever tooling surrounds them.

    portfolio_sites.py list     [--show-slugs]
    portfolio_sites.py check    [--show-slugs]             exit 1 on any problem
    portfolio_sites.py publish  [--apply] [--dir DIR ...]  copy the theme files to the central store
    portfolio_sites.py link     [--apply]                  create store entries and checkout links
    portfolio_sites.py vendor   [--apply] [NAME ...]       write each repo's last-resort fallback copy

Enrollment mirrors the private support-repo workflow. ``config/portfolio-sites/sites.conf`` is the
tracked base and may name only repositories the visibility registry classifies public. A private
repository is enrolled in an ignored, owner-only overlay
(``~/.config/traction-control/portfolio-sites.private.local.conf``), and every entry is verified
against the registry. Output hides private slugs unless ``--show-slugs`` is given.

The central store (``~/.config/traction-control/sites``, owner-only) holds one directory per
repository slug with its ``site.local.toml`` (hostname, port and fronting from the wiring-harness
registry when present) and an ``assets`` link to the shared ``_shared`` directory; each checkout
gets one ignored ``.portfolio`` symlink to its entry. Everything is render-only (a plan) until
``--apply``. See docs/portfolio-sites.md.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "themes"))
import portfolio_site as seam  # noqa: E402

BASE_CONFIG = ROOT / "config" / "portfolio-sites" / "sites.conf"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "traction-control"
OVERLAY_CONFIG = CONFIG_DIR / "portfolio-sites.private.local.conf"
THEMES = ROOT / "themes"
SHARED = "_shared"
PUBLISHED = {
    "theme.css": "portfolio-theme.css",
    "theme.js": "portfolio-theme.js",
    "chrome.js": "portfolio-chrome.js",
}
VENDORED = {**PUBLISHED, "site.py": "portfolio_site.py"}
MANIFEST = "MANIFEST.sha256"
NAME = __import__("re").compile(r"[a-z0-9][a-z0-9-]{0,40}")
SLUG = __import__("re").compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
REGISTRY_TOOL = ROOT / "scripts" / "repository_visibility.py"


class SiteError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    slug: str
    path: str  # relative to the portfolio root
    vendor_dir: str  # relative to the checkout
    fronting: str
    private: bool


def _safe_relative(value: str, what: str) -> str:
    p = Path(value)
    if not value or p.is_absolute() or ".." in p.parts:
        raise SiteError(f"{what} must be a relative path without '..': {value!r}")
    return value


def parse_config(text: str, *, private: bool, origin: str = "config") -> list[Entry]:
    entries: list[Entry] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 5:
            raise SiteError(f"{origin}:{number}: need name|github_slug|relative_path|vendor_dir|fronting")
        name, slug, path, vendor_dir, fronting = parts
        if not NAME.fullmatch(name):
            raise SiteError(f"{origin}:{number}: bad name {name!r}")
        if not SLUG.fullmatch(slug):
            raise SiteError(f"{origin}:{number}: bad GitHub slug")
        if fronting not in seam.FRONTING:
            raise SiteError(f"{origin}:{number}: fronting must be one of {', '.join(seam.FRONTING)}")
        entries.append(
            Entry(
                name,
                slug,
                _safe_relative(path, f"{origin}:{number} path"),
                _safe_relative(vendor_dir, f"{origin}:{number} vendor_dir"),
                fronting,
                private,
            )
        )
    return entries


def classify(slug: str) -> str:
    """public / private / unclassified, from the visibility registry."""
    registry = ROOT / "config" / "repository-visibility"
    result = subprocess.run(
        [
            sys.executable,
            str(REGISTRY_TOOL),
            "classify",
            "--private",
            str(registry / "private.local.json"),
            "--public",
            str(registry / "public.local.json"),
            "--slug",
            slug,
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "unclassified"


def _exposed_by_git(path: Path) -> bool:
    """True when the file sits in a git work tree without being ignored (or is tracked)."""
    inside = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True
    )
    if inside.returncode != 0:
        return False
    tracked = subprocess.run(["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name], capture_output=True)
    ignored = subprocess.run(["git", "-C", str(path.parent), "check-ignore", "-q", path.name])
    return tracked.returncode == 0 or ignored.returncode != 0


def load_entries(
    base: Path = BASE_CONFIG,
    overlay: Path = OVERLAY_CONFIG,
    classifier: Callable[[str], str] = classify,
) -> list[Entry]:
    """Tracked public entries plus the verified private overlay; fails closed."""
    entries = parse_config(base.read_text(encoding="utf-8"), private=False, origin=base.name) if base.is_file() else []
    for entry in entries:
        got = classifier(entry.slug)
        if got != "public":
            raise SiteError(f"{base.name}: {entry.name} is {got}, not public; enroll it in the private overlay")
    if overlay.exists():
        if overlay.is_symlink() or not overlay.is_file():
            raise SiteError(f"private overlay must be a regular file: {overlay.name}")
        if overlay.stat().st_mode & 0o077:
            raise SiteError("private overlay must be owner-only (chmod 600)")
        if _exposed_by_git(overlay):
            raise SiteError("private overlay must not be tracked or sit unignored in a git work tree")
        private = parse_config(overlay.read_text(encoding="utf-8"), private=True, origin=overlay.name)
        names = {e.name for e in entries}
        for entry in private:
            if entry.name in names:
                raise SiteError(f"duplicate site name {entry.name!r} in the overlay")
            got = classifier(entry.slug)
            if got != "private":
                raise SiteError(f"overlay entry {entry.name} is {got}, not private; track public repos in sites.conf")
            names.add(entry.name)
        entries += private
    return entries


def label(entry: Entry, show_slugs: bool) -> str:
    return f"{entry.name} ({entry.slug})" if (show_slugs or not entry.private) else f"{entry.name} (private)"


def store_dir(store: Path, entry: Entry) -> Path:
    return store / seam.slug_dirname(entry.slug)


def registry_binding(portfolio: Path, entry: Entry) -> dict[str, object]:
    """hostname / port / ingress for this repository from the wiring-harness registry, if present."""
    base = portfolio / "util-repos" / "wiring-harness"
    for name in ("services.local.toml", "services.toml"):
        path = base / name
        if not path.is_file():
            continue
        try:
            services = tomllib.loads(path.read_text(encoding="utf-8")).get("services", [])
        except (OSError, tomllib.TOMLDecodeError):
            continue
        for svc in services:
            if str(svc.get("owner_repo", "")).lstrip("./") == entry.path:
                out: dict[str, object] = {}
                if svc.get("ingress") in seam.FRONTING:
                    out["fronting"] = svc["ingress"]
                if svc.get("hostname"):
                    out["hostname"] = str(svc["hostname"])
                if isinstance(svc.get("port"), int):
                    out["port"] = svc["port"]
                return out
    return {}


def site_toml(entry: Entry, binding: dict[str, object]) -> str:
    settings = {"fronting": entry.fronting, **binding}
    lines = ["# Generated by traction-control scripts/portfolio_sites.py link; owner-only, never tracked.", "[site]", f'slug = "{entry.slug}"']
    for key, value in settings.items():
        lines.append(f"{key} = {value!r}".replace("'", '"') if isinstance(value, str) else f"{key} = {value}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def publish(store: Path, extra_dirs: list[Path], apply: bool) -> list[str]:
    """Copy the rendered theme files into ``store/_shared`` (and any extra directories)."""
    notes = []
    for target in [store / SHARED, *extra_dirs]:
        for published, source in PUBLISHED.items():
            src = THEMES / source
            if not src.is_file():
                raise SiteError(f"missing {src}; run scripts/render_portfolio_theme.py")
            dest = target / published
            same = dest.is_file() and dest.read_bytes() == src.read_bytes()
            notes.append(f"{'ok     ' if same else 'publish'} {dest}")
            if apply and not same:
                target.mkdir(parents=True, exist_ok=True)
                if target == store / SHARED:
                    os.chmod(target, 0o700)
                _atomic_write(dest, src.read_bytes(), 0o644)
    return notes


def link(
    entries: list[Entry], portfolio: Path, store: Path, apply: bool, show_slugs: bool
) -> tuple[list[str], list[str]]:
    plan, problems = [], []
    for entry in entries:
        who = label(entry, show_slugs)
        checkout = portfolio / entry.path
        if not (checkout / ".git").exists():
            problems.append(f"{who}: no checkout at {entry.path}")
            continue
        found = seam.origin_slug(checkout)
        if found != entry.slug.lower():
            problems.append(f"{who}: the checkout's origin is not the enrolled repository")
            continue
        ignored = subprocess.run(["git", "-C", str(checkout), "check-ignore", "-q", ".portfolio"]).returncode == 0
        if not ignored:
            problems.append(f"{who}: add /.portfolio to the repository's .gitignore first")
            continue
        entry_dir = store_dir(store, entry)
        link_path = checkout / ".portfolio"
        if link_path.exists() or link_path.is_symlink():
            if not link_path.is_symlink():
                problems.append(f"{who}: .portfolio exists and is not a symlink")
                continue
            if Path(os.readlink(link_path)).resolve() != entry_dir.resolve() and apply:
                link_path.unlink()
        binding = registry_binding(portfolio, entry)
        text = site_toml(entry, binding)
        current = (entry_dir / "site.local.toml").read_text(encoding="utf-8") if (entry_dir / "site.local.toml").is_file() else None
        plan.append(f"{'ok  ' if current == text and link_path.is_symlink() else 'link'} {who}")
        if apply:
            store.mkdir(parents=True, exist_ok=True)
            os.chmod(store, 0o700)
            entry_dir.mkdir(exist_ok=True)
            os.chmod(entry_dir, 0o700)
            if current != text:
                _atomic_write(entry_dir / "site.local.toml", text.encode(), 0o600)
            assets = entry_dir / "assets"
            if not assets.is_symlink():
                assets.symlink_to(Path("..") / SHARED)
            if not link_path.is_symlink():
                link_path.symlink_to(entry_dir)
    return plan, problems


def manifest_text(directory: Path) -> str:
    lines = []
    for name in sorted(VENDORED):
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    return "\n".join(lines) + "\n"


def vendor(entry: Entry, portfolio: Path, apply: bool) -> str:
    target = portfolio / entry.path / entry.vendor_dir
    sources = {name: THEMES / source for name, source in VENDORED.items()}
    want = {name: p.read_bytes() for name, p in sources.items()}
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp)
        for name, data in want.items():
            (staged / name).write_bytes(data)
        manifest = manifest_text(staged)
    current = {n: (target / n).read_bytes() if (target / n).is_file() else None for n in want}
    current_manifest = (target / MANIFEST).read_text(encoding="utf-8") if (target / MANIFEST).is_file() else None
    fresh = all(current[n] == want[n] for n in want) and current_manifest == manifest
    if apply and not fresh:
        target.mkdir(parents=True, exist_ok=True)
        for name, data in want.items():
            (target / name).write_bytes(data)
        (target / MANIFEST).write_text(manifest, encoding="utf-8")
    return "ok    " if fresh else ("vendor" if not apply else "wrote ")


def check(entries: list[Entry], portfolio: Path, store: Path, show_slugs: bool) -> list[str]:
    problems = []
    for entry in entries:
        who = label(entry, show_slugs)
        checkout = portfolio / entry.path
        if not (checkout / ".git").exists():
            problems.append(f"{who}: no checkout at {entry.path}")
            continue
        site = seam.resolve(checkout, vendored=checkout / entry.vendor_dir, env={seam.STORE_ENV: str(store)})
        if site.source not in ("link", "slug"):
            problems.append(f"{who}: not linked to the store (run link --apply)")
        if vendor(entry, portfolio, apply=False).strip() != "ok":
            problems.append(f"{who}: vendored fallback is stale (run vendor --apply)")
    shared = store / SHARED
    for published, source in PUBLISHED.items():
        dest = shared / published
        if not dest.is_file() or dest.read_bytes() != (THEMES / source).read_bytes():
            problems.append(f"store: {published} is missing or stale (run publish --apply)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "check", "publish", "link", "vendor"))
    parser.add_argument("names", nargs="*", help="vendor: only these site names")
    parser.add_argument("--apply", action="store_true", help="write changes (default: show the plan)")
    parser.add_argument("--show-slugs", action="store_true", help="show private repository slugs")
    parser.add_argument("--store", type=Path, help="central store (default: ~/.config/traction-control/sites)")
    parser.add_argument("--dir", type=Path, action="append", default=[], help="publish: extra directory")
    parser.add_argument("--portfolio-root", type=Path, default=ROOT.parents[1])
    parser.add_argument("--base-config", type=Path, default=BASE_CONFIG)
    parser.add_argument("--private-config", type=Path, default=OVERLAY_CONFIG)
    args = parser.parse_args(argv)
    store = args.store or seam.default_store()
    try:
        entries = load_entries(args.base_config, args.private_config)
        if args.command == "list":
            for e in entries:
                print(f"{label(e, args.show_slugs):40} {e.fronting:22} {e.path}")
            return 0
        if args.command == "publish":
            print("\n".join(publish(store, args.dir, args.apply)))
            return 0
        if args.command == "link":
            plan, problems = link(entries, args.portfolio_root, store, args.apply, args.show_slugs)
            print("\n".join(plan))
        elif args.command == "vendor":
            chosen = [e for e in entries if not args.names or e.name in args.names]
            for e in chosen:
                print(f"{vendor(e, args.portfolio_root, args.apply)} {label(e, args.show_slugs)}")
            return 0
        else:
            problems = check(entries, args.portfolio_root, store, args.show_slugs)
    except SiteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"problem: {problem}", file=sys.stderr)
    if not args.apply and args.command == "link":
        print("(plan only; re-run with --apply)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
