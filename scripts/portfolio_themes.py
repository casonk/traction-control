#!/usr/bin/env python3
"""Your own portfolio themes: an owner-only layer over themes/themes.toml.

    portfolio_themes.py show [--json]      the merged themes, palette, emoji and what is active
    portfolio_themes.py apply --stdin      replace the local layer from JSON, render and publish it
    portfolio_themes.py render             render and publish the current local layer again
    portfolio_themes.py reset              drop the local layer and go back to the tracked themes
    portfolio_themes.py path               where everything lives

The tracked themes (themes/themes.toml) stay the portfolio's defaults and are never edited here. Your
changes live in ``~/.config/traction-control/themes.local.toml`` (owner-only, never committed):

    [active]                  which themes are offered, in menu order (omit for all of them)
    [palette]                 named colours you can pick from when editing a theme
    [emoji]                   named emoji you can pick from for icons
    [override.<built-in>]     change a tracked theme: label, icon, description, stripe, any token
    [themes.<name>]           a theme of your own: ``based_on`` a tracked theme, plus any changes

The merged result is rendered once into ``~/.config/traction-control/themes/rendered/`` and the store's
``_shared`` files are *symlinks* to it, so there is one rendered copy rather than one per consumer. (The
Caddy-served directory has to stay a copy: Caddy cannot read through an owner-only home directory.)
Everything goes through the same validation as the tracked file, plus limits on names and text because the
values end up in CSS and HTML.

Standard library only (Python 3.11+ for tomllib).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_portfolio_theme as base  # noqa: E402

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "traction-control"
LOCAL_PATH = CONFIG_DIR / "themes.local.toml"
RENDERED_DIR = CONFIG_DIR / "themes" / "rendered"
STORE_SHARED = CONFIG_DIR / "sites" / "_shared"
PUBLISHED = {
    "theme.css": "portfolio-theme.css",
    "theme.js": "portfolio-theme.js",
    "chrome.js": "portfolio-chrome.js",
}
THEME_FIELDS = ("label", "description", "scheme", "icon", "stripe", *base.TOKENS)
LIBRARY_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
MAX_THEMES, MAX_LIBRARY = 40, 200
TOP_LEVEL = {"active", "palette", "emoji", "override", "themes"}
# Text that ends up inside a CSS comment, a menu label or an attribute: no markup, no comment markers.
UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f<>&\"'`\\]|/\*|\*/")


class LocalThemeError(base.ThemeError):
    pass


# -- the local layer ---------------------------------------------------------


def local_path() -> Path:
    """Where the local layer is: ``PORTFOLIO_THEMES_LOCAL`` if set (read each time, for tests), else the default."""
    return Path(os.environ["PORTFOLIO_THEMES_LOCAL"]) if os.environ.get("PORTFOLIO_THEMES_LOCAL") else LOCAL_PATH


def rendered_for(store: Path) -> Path:
    """Where the rendered files go for a site store: beside it, in ``themes/rendered``."""
    return store.parent / "themes" / "rendered"


def empty() -> dict[str, Any]:
    return {"active": None, "palette": {}, "emoji": {}, "override": {}, "themes": {}}


def _text(what: str, value: Any, limit: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or UNSAFE_TEXT.search(value):
        raise LocalThemeError(f"{what}: 1 to {limit} characters, with no markup or comment markers")
    return value


def _glyph(what: str, value: Any) -> str:
    # Non-ASCII only: an emoji or symbol, never markup. Up to 8 code points covers flags and ZWJ sequences.
    if not isinstance(value, str) or not 1 <= len(value) <= 8 or any(ord(c) < 128 for c in value):
        raise LocalThemeError(f"{what}: 1 to 8 emoji or symbol characters")
    return value


def _fields(what: str, table: Any, extra: tuple[str, ...] = ()) -> dict[str, str]:
    if not isinstance(table, dict):
        raise LocalThemeError(f"{what}: must be a table")
    unknown = sorted(set(table) - set(THEME_FIELDS) - set(extra))
    if unknown:
        raise LocalThemeError(f"{what}: unknown keys {unknown}")
    out: dict[str, str] = {}
    for key, value in table.items():
        if key in base.TOKENS:
            if not base.HEX.fullmatch(str(value)):
                raise LocalThemeError(f"{what}: {key} must be a #rrggbb colour")
            out[key] = str(value).lower()
        elif key == "label":
            out[key] = _text(f"{what}.label", value, 32)
        elif key == "description":
            out[key] = _text(f"{what}.description", value, 120)
        elif key == "icon":
            out[key] = _glyph(f"{what}.icon", value)
        elif key == "scheme":
            if value not in ("dark", "light"):
                raise LocalThemeError(f"{what}: scheme must be dark or light")
            out[key] = value
        elif key == "stripe":
            if not base.STRIPE.fullmatch(str(value)):
                raise LocalThemeError(f"{what}: stripe must be a plain CSS background value")
            out[key] = str(value)
        elif key == "based_on":
            if not base.NAME.fullmatch(str(value)):
                raise LocalThemeError(f"{what}: based_on must name a tracked theme")
            out[key] = str(value)
    return out


def clean(raw: dict[str, Any]) -> dict[str, Any]:
    """The local layer from parsed TOML or JSON, validated; anything unexpected is an error."""
    unknown = sorted(set(raw) - TOP_LEVEL)
    if unknown:
        raise LocalThemeError(f"unknown top-level keys {unknown}")
    model = empty()
    active = raw.get("active")
    if isinstance(active, dict):  # the TOML form is [active] themes = [...]
        active = active.get("themes")
    if active is not None:
        if not isinstance(active, list) or not all(isinstance(n, str) for n in active):
            raise LocalThemeError("active: a list of theme names")
        model["active"] = list(dict.fromkeys(active))
    for key, check in (("palette", "colour"), ("emoji", "glyph")):
        table = raw.get(key, {})
        if not isinstance(table, dict) or len(table) > MAX_LIBRARY:
            raise LocalThemeError(f"{key}: a table of at most {MAX_LIBRARY} entries")
        for name, value in table.items():
            if not LIBRARY_NAME.fullmatch(name):
                raise LocalThemeError(f"{key}.{name}: use lowercase letters, digits and -")
            if check == "colour":
                if not base.HEX.fullmatch(str(value)):
                    raise LocalThemeError(f"palette.{name} must be a #rrggbb colour")
                model[key][name] = str(value).lower()
            else:
                model[key][name] = _glyph(f"emoji.{name}", value)
    for name, table in raw.get("override", {}).items():
        model["override"][name] = _fields(f"override.{name}", table)
    themes = raw.get("themes", {})
    if len(themes) > MAX_THEMES:
        raise LocalThemeError(f"at most {MAX_THEMES} themes of your own")
    for name, table in themes.items():
        if not base.NAME.fullmatch(name):
            raise LocalThemeError(f"themes.{name}: use lowercase letters, digits and -")
        model["themes"][name] = _fields(f"themes.{name}", table, extra=("based_on",))
    return model


def load_local(path: Path = LOCAL_PATH) -> dict[str, Any]:
    """The local layer, or an empty one when there is no file."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    except tomllib.TOMLDecodeError as exc:
        raise LocalThemeError(f"{path}: {exc}") from exc
    return clean(raw)


def _toml_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)  # validated text has no control characters


def dump_local(model: dict[str, Any]) -> str:
    """The local layer as TOML (every key and value was validated, so this needs no escaping logic)."""
    lines = ["# Your portfolio themes. Owner-only; written by scripts/portfolio_themes.py. See docs/system-theme.md.", ""]
    if model["active"] is not None:
        lines += ["[active]", f"themes = {_toml_value(model['active'])}", ""]
    for section in ("palette", "emoji"):
        if model[section]:
            lines.append(f"[{section}]")
            lines += [f"{k} = {_toml_value(v)}" for k, v in sorted(model[section].items())]
            lines.append("")
    for section in ("override", "themes"):
        for name, table in model[section].items():
            lines.append(f"[{section}.{name}]")
            lines += [f"{k} = {_toml_value(v)}" for k, v in table.items()]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# -- merging ----------------------------------------------------------------


def merge(document: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """The tracked themes with the local layer applied: a document ``render_portfolio_theme`` accepts."""
    merged = copy.deepcopy(document)
    themes = merged["themes"]
    builtin = set(themes)
    for name, changes in model["override"].items():
        if name not in builtin:
            raise LocalThemeError(f"override.{name}: not a tracked theme")
        themes[name].update({k: v for k, v in changes.items() if k != "based_on"})
    for name, table in model["themes"].items():
        if name in builtin:
            raise LocalThemeError(f"themes.{name}: a tracked theme already has this name; use [override.{name}]")
        parent = table.get("based_on")
        if parent not in builtin:
            raise LocalThemeError(f"themes.{name}: based_on must name a tracked theme")
        if "label" not in table:
            raise LocalThemeError(f"themes.{name}: needs a label")
        themes[name] = {**copy.deepcopy(themes[parent]), **{k: v for k, v in table.items() if k != "based_on"}}
    if model["active"] is not None:
        missing = [n for n in model["active"] if n not in themes]
        if missing:
            raise LocalThemeError(f"active: unknown theme(s) {missing}")
        merged["themes"] = {n: themes[n] for n in model["active"]}
    chosen = merged["themes"]
    meta = merged["meta"]
    for key, scheme in (("default_dark", "dark"), ("default_light", "light")):
        if meta[key] not in chosen or chosen[meta[key]]["scheme"] != scheme:
            fallback = next((n for n, t in chosen.items() if t["scheme"] == scheme), None)
            if fallback is None:
                raise LocalThemeError(f"active: keep at least one {scheme} theme")
            meta[key] = fallback
    meta["aliases"] = {o: n for o, n in meta.get("aliases", {}).items() if n in chosen and o not in chosen}
    base.validate(merged)
    return merged


def merged_document(path: Path = LOCAL_PATH) -> dict[str, Any]:
    return merge(base.load(), load_local(path))


# -- publishing ------------------------------------------------------------


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _owner_only(directory: Path, stop: Path) -> None:
    """Create *directory* and make it and its parents up to *stop* 0700."""
    directory.mkdir(parents=True, exist_ok=True)
    current = directory
    while True:
        os.chmod(current, 0o700)
        if current == stop or current.parent == current:
            break
        current = current.parent


def render_store(document: dict[str, Any], rendered: Path = RENDERED_DIR) -> dict[str, Path]:
    """Write the rendered theme files once, owner-only, and return where each was written."""
    _owner_only(rendered, rendered.parent.parent)
    outputs = base.render(document)
    written = {}
    for published, source in PUBLISHED.items():
        _atomic_write(rendered / published, outputs[source], 0o600)
        written[published] = rendered / published
    return written


def link_shared(rendered: Path = RENDERED_DIR, shared: Path = STORE_SHARED) -> list[str]:
    """Make the store's ``_shared`` theme files symlinks to the rendered ones; returns what changed.

    The link is relative so the whole ``traction-control`` config tree can be moved. Replacing a copy
    with a link is atomic (a new link is renamed over it), so a reader never sees a missing file.
    """
    if not shared.is_dir():
        return []
    changed = []
    for published in PUBLISHED:
        dest, target = shared / published, os.path.relpath(rendered / published, shared)
        if dest.is_symlink() and os.readlink(dest) == target:
            continue
        tmp = shared / f".{published}.link"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(target)
        os.replace(tmp, dest)
        changed.append(published)
    return changed


def unlink_shared(shared: Path = STORE_SHARED) -> list[str]:
    """Replace any symlinked theme file in ``_shared`` with a copy of the tracked one (after a reset)."""
    if not shared.is_dir():
        return []
    outputs = base.render(base.load())
    changed = []
    for published, source in PUBLISHED.items():
        dest = shared / published
        if dest.is_symlink():
            _atomic_write(dest, outputs[source], 0o644)  # os.replace swaps the link itself
            changed.append(published)
    return changed


def publish(path: Path = LOCAL_PATH, rendered: Path = RENDERED_DIR, shared: Path = STORE_SHARED) -> dict[str, Any]:
    """Merge, render and link; the one call everything else goes through."""
    document = merged_document(path)
    written = render_store(document, rendered)
    return {
        "themes": list(document["themes"]),
        "rendered": [str(p) for p in written.values()],
        "linked": link_shared(rendered, shared),
    }


def problems(path: Path = LOCAL_PATH, rendered: Path = RENDERED_DIR, shared: Path = STORE_SHARED) -> list[str]:
    """Why the store's theme files are not the merged render (empty when they are)."""
    found = []
    outputs = base.render(merged_document(path))
    for published, source in PUBLISHED.items():
        dest = shared / published
        if not dest.is_symlink() or dest.resolve() != (rendered / published).resolve():
            found.append(f"store: {published} is not a link to {rendered / published}")
        elif dest.read_text(encoding="utf-8") != outputs[source]:
            found.append(f"store: {published} is stale (run portfolio_themes.py render)")
    return found


# -- the editor's view -----------------------------------------------------


def state(path: Path = LOCAL_PATH) -> dict[str, Any]:
    """Everything an editor needs: every theme (with where it came from), the libraries, the active set."""
    document, model = base.load(), load_local(path)
    everything = merge(document, {**model, "active": None})
    builtin = set(document["themes"])
    chosen = merged_document(path)
    themes = []
    for name, theme in everything["themes"].items():
        themes.append(
            {
                "name": name,
                "origin": "builtin" if name in builtin else "custom",
                "modified": name in model["override"],
                "based_on": model["themes"].get(name, {}).get("based_on"),
                "active": name in chosen["themes"],
                **{k: theme.get(k, "") for k in ("label", "description", "scheme", "icon", "stripe")},
                "tokens": {t: theme[t] for t in base.TOKENS},
            }
        )
    return {
        "themes": themes,
        "active": list(chosen["themes"]),
        "palette": model["palette"],
        "emoji": model["emoji"],
        "defaults": {"dark": chosen["meta"]["default_dark"], "light": chosen["meta"]["default_light"]},
        "tokens": list(base.TOKENS),
        "paths": {"local": str(path), "rendered": str(RENDERED_DIR), "shared": str(STORE_SHARED)},
    }


def from_state(desired: dict[str, Any]) -> dict[str, Any]:
    """The local layer that produces *desired* (the shape ``state`` returns, as an editor sends it back).

    A tracked theme becomes an override holding only what differs from the tracked file, so an editor that
    sends every field does not freeze the tracked values; a theme with any other name becomes your own.
    """
    document = base.load()
    builtin = document["themes"]
    raw: dict[str, Any] = {"palette": desired.get("palette", {}), "emoji": desired.get("emoji", {})}
    if desired.get("active") is not None:
        raw["active"] = desired["active"]
    raw["override"], raw["themes"] = {}, {}
    for entry in desired.get("themes", []):
        name = entry.get("name")
        fields = {k: entry[k] for k in THEME_FIELDS if k in entry and entry[k] not in (None, "")}
        fields.update({k: v for k, v in entry.get("tokens", {}).items() if k in base.TOKENS})
        if name in builtin:
            changed = {k: v for k, v in fields.items() if str(builtin[name].get(k, "")).lower() != str(v).lower()}
            if changed:
                raw["override"][name] = changed
        else:
            raw["themes"][name] = {"based_on": entry.get("based_on") or _nearest(builtin, fields), **fields}
    return clean(raw)


def _nearest(builtin: dict[str, Any], fields: dict[str, Any]) -> str:
    """A tracked theme of the same scheme to start a new theme from when the editor named none."""
    scheme = fields.get("scheme", "dark")
    return next((n for n, t in builtin.items() if t["scheme"] == scheme), next(iter(builtin)))


def apply(desired: dict[str, Any], path: Path = LOCAL_PATH, rendered: Path = RENDERED_DIR, shared: Path = STORE_SHARED) -> dict[str, Any]:
    """Validate *desired*, keep it as the local layer (owner-only) and publish it. Nothing changes on error."""
    model = from_state(desired)
    merge(base.load(), model)  # reject anything that would not render, before touching a file
    _atomic_write(path, dump_local(model), 0o600)
    return publish(path, rendered, shared)


def reset(path: Path = LOCAL_PATH, rendered: Path = RENDERED_DIR, shared: Path = STORE_SHARED) -> dict[str, Any]:
    path.unlink(missing_ok=True)
    restored = unlink_shared(shared)
    for published in PUBLISHED:
        (rendered / published).unlink(missing_ok=True)
    return {"restored": restored}


# -- command line ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("show", "apply", "render", "reset", "path", "check"))
    parser.add_argument("--json", action="store_true", help="show: machine-readable output")
    parser.add_argument("--stdin", action="store_true", help="apply: read the desired state as JSON on stdin")
    parser.add_argument("--local", type=Path, default=None, help=f"the local layer (default {LOCAL_PATH})")
    parser.add_argument("--rendered", type=Path, default=RENDERED_DIR)
    parser.add_argument("--shared", type=Path, default=STORE_SHARED)
    args = parser.parse_args(argv)
    args.local = args.local or local_path()
    try:
        if args.command == "path":
            print(f"local     {args.local}\nrendered  {args.rendered}\nshared    {args.shared}")
        elif args.command == "show":
            data = state(args.local)
            if args.json:
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                for t in data["themes"]:
                    marks = ("*" if t["active"] else " ") + ("~" if t["modified"] else " ")
                    print(f"{marks} {t['name']:<16} {t['icon']} {t['label']} ({t['origin']}, {t['scheme']})")
                print(f"\n* active, ~ changed by you; {len(data['palette'])} colours and {len(data['emoji'])} emoji saved")
        elif args.command == "apply":
            if not args.stdin:
                parser.error("apply needs --stdin")
            print(json.dumps(apply(json.load(sys.stdin), args.local, args.rendered, args.shared), ensure_ascii=False))
        elif args.command == "render":
            print(json.dumps(publish(args.local, args.rendered, args.shared), ensure_ascii=False))
        elif args.command == "reset":
            print(json.dumps(reset(args.local, args.rendered, args.shared)))
        elif args.command == "check":
            found = problems(args.local, args.rendered, args.shared) if args.local.exists() else []
            print("\n".join(found))
            return 1 if found else 0
    except (base.ThemeError, OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
