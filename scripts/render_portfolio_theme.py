#!/usr/bin/env python3
"""Render the portfolio's shared theme files from themes/themes.toml.

    render_portfolio_theme.py             write themes/portfolio-theme.{css,js}
    render_portfolio_theme.py --check     exit 1 if the written files are stale
    render_portfolio_theme.py --install DIR
                                          copy the rendered files to a consuming repo

The CSS defines every token for every theme under ``[data-theme="<name>"]``, with
the default dark theme on ``:root`` and a ``prefers-color-scheme: light`` rule so
a page with no script still follows the system. The JavaScript picks the theme
(saved choice, ``?theme=`` query, then the system preference), saves changes, and
fills any ``[data-theme-switcher]`` element with a theme chooser.

Standard library only (Python 3.11+ for tomllib).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "themes" / "themes.toml"
OUTPUTS = ("portfolio-theme.css", "portfolio-theme.js", "portfolio-chrome.js")
TOKENS = (
    "bg",
    "surface",
    "surface2",
    "border",
    "text",
    "muted",
    "accent",
    "accent-dim",
    "accent-ink",
    "green",
    "green-dim",
    "red",
    "red-dim",
    "yellow",
    "yellow-dim",
    "purple",
    "purple-dim",
)
HEX = re.compile(r"#[0-9a-fA-F]{6}")
STRIPE = re.compile(r"[A-Za-z0-9#%.,() \-]{1,200}")
NAME = re.compile(r"[a-z][a-z0-9-]{0,23}")
HEADER = "Generated from themes/themes.toml by scripts/render_portfolio_theme.py; do not edit."


class ThemeError(ValueError):
    pass


def load(path: Path = SOURCE) -> dict:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    themes = document.get("themes", {})
    meta = document.get("meta", {})
    if not themes:
        raise ThemeError("no [themes.*] tables")
    for name, theme in themes.items():
        if not NAME.fullmatch(name):
            raise ThemeError(f"theme name {name!r}: use lowercase letters, digits and -")
        if theme.get("scheme") not in ("dark", "light"):
            raise ThemeError(f"theme {name}: scheme must be dark or light")
        if not theme.get("label"):
            raise ThemeError(f"theme {name}: needs a label")
        for token in TOKENS:
            if not HEX.fullmatch(str(theme.get(token, ""))):
                raise ThemeError(f"theme {name}: {token} must be a #rrggbb colour")
        stripe = theme.get("stripe")
        if stripe is not None and not STRIPE.fullmatch(str(stripe)):
            raise ThemeError(f"theme {name}: stripe must be a plain CSS background value")
        extra = set(theme) - set(TOKENS) - {"label", "description", "scheme", "icon", "stripe"}
        if extra:
            raise ThemeError(f"theme {name}: unknown keys {sorted(extra)}")
    for key in ("default_dark", "default_light"):
        if meta.get(key) not in themes:
            raise ThemeError(f"meta.{key} must name a theme")
    if themes[meta["default_dark"]]["scheme"] != "dark":
        raise ThemeError("default_dark must be a dark theme")
    if themes[meta["default_light"]]["scheme"] != "light":
        raise ThemeError("default_light must be a light theme")
    for old, new in meta.get("aliases", {}).items():
        if old in themes or new not in themes or not NAME.fullmatch(old):
            raise ThemeError(f"meta.aliases.{old}: an old name must not be a theme, and must point at one")
    if not re.fullmatch(r"[a-z][a-z-]{2,31}", str(meta.get("storage_key", ""))):
        raise ThemeError("meta.storage_key must be a short lowercase key")
    return document


def _block(selector: str, theme: dict, prefix: str = "") -> str:
    lines = [f"{selector} {{"]
    if not prefix:
        lines.append(f"  color-scheme: {theme['scheme']};")
    lines += [f"  --{prefix}{token}: {theme[token].lower()};" for token in TOKENS]
    lines.append(f"  --{prefix}stripe: {theme.get('stripe', 'transparent')};")
    lines.append("}")
    return "\n".join(lines)


def render_css(document: dict) -> str:
    themes, meta = document["themes"], document["meta"]
    dark, light = meta["default_dark"], meta["default_light"]
    parts = [f"/* {HEADER} */", _block(":root", themes[dark])]
    parts.append(
        "@media (prefers-color-scheme: light) {\n"
        + _block("  :root:not([data-theme])", themes[light]).replace("\n", "\n  ").rstrip()
        + "\n}"
    )
    for name, theme in themes.items():
        parts.append(f'/* {theme["label"]}: {theme.get("description", "")} */')
        parts.append(_block(f':root[data-theme="{name}"]', theme))
    return "\n".join(parts) + "\n"


def render_scoped_css(document: dict) -> str:
    """The tokens as --pf-* on .pf-topbar only, so a page keeps its own palette."""
    themes, meta = document["themes"], document["meta"]
    parts = [_block(".pf-topbar", themes[meta["default_dark"]], "pf-")]
    light = _block("  html:not([data-theme]) .pf-topbar", themes[meta["default_light"]], "pf-")
    parts.append("@media (prefers-color-scheme: light) {\n" + light.replace("\n", "\n  ").rstrip() + "\n}")
    for name, theme in themes.items():
        parts.append(_block(f'html[data-theme="{name}"] .pf-topbar', theme, "pf-"))
    return "\n".join(parts) + "\n"


def render_chrome(document: dict) -> str:
    """One script for pages served behind the shared Caddy: theme, switcher and top bar."""
    css = render_scoped_css(document) + (SOURCE.parent / "topbar.css").read_text(encoding="utf-8")
    theme_js = render_js(document)
    top = (SOURCE.parent / "topbar.js").read_text(encoding="utf-8")
    return (
        f"/* {HEADER} */\n"
        "(function () {\n"
        "  if (window.__portfolioChrome) return;\n"
        "  window.__portfolioChrome = true;\n"
        '  var style = document.createElement("style");\n'
        f"  style.textContent = {json.dumps(css)};\n"
        "  document.head.appendChild(style);\n"
        "})();\n"
        "if (!window.PortfolioTheme) {\n" + theme_js + "}\n" + top
    )


def render_js(document: dict) -> str:
    themes, meta = document["themes"], document["meta"]
    listing = {
        n: {
            "label": t["label"],
            "scheme": t["scheme"],
            "icon": t.get("icon", "☀" if t["scheme"] == "light" else "☾"),
            "swatch": [t["bg"].lower(), t["accent"].lower(), t["text"].lower()],
        }
        for n, t in themes.items()
    }
    return f"""/* {HEADER} */
(function () {{
  var THEMES = {json.dumps(listing, indent=2)};
  var KEY = {json.dumps(meta["storage_key"])};
  var ALIASES = {json.dumps(meta.get("aliases", {}))};
  function known(name) {{ name = ALIASES[name] || name; return THEMES[name] ? name : null; }}
  var DARK = {json.dumps(meta["default_dark"])}, LIGHT = {json.dumps(meta["default_light"])};
  var root = document.documentElement;
  function saved() {{
    try {{ return localStorage.getItem(KEY); }} catch (e) {{ return null; }}
  }}
  function pick() {{
    var asked = new URLSearchParams(location.search).get("theme");
    if (known(asked)) return known(asked);
    var kept = saved();
    if (known(kept)) return known(kept);
    return window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? LIGHT : DARK;
  }}
  var ORDER = Object.keys(THEMES);
  function current() {{ return root.getAttribute("data-theme"); }}
  function following(name) {{ return ORDER[(ORDER.indexOf(name) + 1) % ORDER.length]; }}
  function paintCycle(el, name) {{
    var theme = THEMES[name], target = THEMES[following(name)];
    var icon = el.querySelector("[data-theme-icon]"), label = el.querySelector("[data-theme-name]");
    if (icon || label) {{
      if (icon) icon.textContent = theme.icon;
      if (label) label.textContent = theme.label;
    }} else {{
      el.textContent = theme.icon + " " + theme.label;
    }}
    var text = "Theme: " + theme.label + ". Switch to " + target.label + ".";
    el.setAttribute("aria-label", text);
    el.setAttribute("title", text);
  }}
  function apply(name) {{
    root.setAttribute("data-theme", name);
    document.querySelectorAll("[data-theme-switcher]").forEach(function (el) {{ el.value = name; }});
    document.querySelectorAll("[data-theme-cycle]").forEach(function (el) {{ paintCycle(el, name); }});
    document.querySelectorAll("[data-theme-menu]").forEach(function (menu) {{ paintMenu(menu, name); }});
  }}
  function paintMenu(menu, name) {{
    menu.querySelectorAll("[data-theme-choice]").forEach(function (item) {{
      item.setAttribute("aria-checked", String(item.getAttribute("data-theme-choice") === name));
    }});
  }}
  function fill(menu) {{
    if (menu.pfFilled) return;
    menu.pfFilled = true;
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Themes");
    ORDER.forEach(function (name) {{
      var theme = THEMES[name], item = document.createElement("button");
      item.type = "button";
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("data-theme-choice", name);
      var swatch = document.createElement("span");
      swatch.className = "pf-swatch";
      swatch.style.background = "linear-gradient(90deg," + theme.swatch[0] + " 0 34%," +
        theme.swatch[1] + " 34% 67%," + theme.swatch[2] + " 67%)";
      var label = document.createElement("span");
      label.textContent = theme.icon + " " + theme.label;
      var kind = document.createElement("small");
      kind.textContent = theme.scheme;
      item.appendChild(swatch); item.appendChild(label); item.appendChild(kind);
      item.addEventListener("click", function () {{
        set(name);
        var open = menu.closest && menu.closest("details");
        if (open) open.open = false;
      }});
      menu.appendChild(item);
    }});
    paintMenu(menu, current() || pick());
  }}
  function set(name) {{
    name = known(name);
    if (!name) return;
    try {{ localStorage.setItem(KEY, name); }} catch (e) {{}}
    apply(name);
  }}
  function cycle() {{ set(following(current() || pick())); }}
  function bind(el) {{
    if (el.pfBound) return;
    el.pfBound = true;
    el.addEventListener("click", cycle);
    paintCycle(el, current() || pick());
  }}
  apply(pick());
  window.addEventListener("storage", function (event) {{
    if (event.key === KEY && known(event.newValue)) apply(known(event.newValue));
  }});
  document.addEventListener("DOMContentLoaded", function () {{
    document.querySelectorAll("[data-theme-cycle]").forEach(bind);
    document.querySelectorAll("[data-theme-menu]").forEach(fill);
    document.querySelectorAll("[data-theme-switcher]").forEach(function (el) {{
      if (el.tagName === "SELECT" && !el.options.length) {{
        Object.keys(THEMES).forEach(function (name) {{
          var option = document.createElement("option");
          option.value = name;
          option.textContent = THEMES[name].label;
          el.appendChild(option);
        }});
      }}
      el.setAttribute("aria-label", "Theme");
      el.addEventListener("change", function () {{ set(el.value); }});
    }});
    apply(root.getAttribute("data-theme") || pick());
  }});
  window.PortfolioTheme = {{ themes: THEMES, set: set, cycle: cycle, bind: bind, fill: fill, current: current }};
}})();
"""


def render(document: dict) -> dict[str, str]:
    return {
        "portfolio-theme.css": render_css(document),
        "portfolio-theme.js": render_js(document),
        "portfolio-chrome.js": render_chrome(document),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the outputs are stale")
    parser.add_argument("--install", metavar="DIR", help="copy the rendered files into DIR")
    args = parser.parse_args(argv)
    try:
        outputs = render(load())
    except (ThemeError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    target = SOURCE.parent
    if args.check:
        stale = [
            n
            for n, text in outputs.items()
            if not (target / n).is_file() or (target / n).read_text(encoding="utf-8") != text
        ]
        for name in stale:
            print(f"stale: themes/{name}; run scripts/render_portfolio_theme.py", file=sys.stderr)
        return 1 if stale else 0
    for name, text in outputs.items():
        (target / name).write_text(text, encoding="utf-8")
    if args.install:
        dest = Path(args.install)
        dest.mkdir(parents=True, exist_ok=True)
        for name in outputs:
            shutil.copyfile(target / name, dest / name)
        print(f"installed {', '.join(outputs)} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
