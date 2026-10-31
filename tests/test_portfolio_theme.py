"""The shared theme source is complete, readable, and its rendered files are current."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "render_portfolio_theme", ROOT / "scripts" / "render_portfolio_theme.py"
)
rpt = importlib.util.module_from_spec(spec)
sys.modules["render_portfolio_theme"] = rpt
spec.loader.exec_module(rpt)


def luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ThemeSourceTests(unittest.TestCase):
    def setUp(self):
        self.document = rpt.load()
        self.themes = self.document["themes"]

    def test_there_are_several_themes_in_both_schemes(self):
        self.assertGreaterEqual(len(self.themes), 3)
        self.assertEqual({t["scheme"] for t in self.themes.values()}, {"dark", "light"})

    def test_text_is_readable_in_every_theme(self):
        for name, t in self.themes.items():
            for fg, bg, floor in (
                ("text", "bg", 4.5),
                ("text", "surface", 4.5),
                ("text", "surface2", 4.5),
                ("muted", "bg", 4.5),
                ("muted", "surface", 4.5),
                ("accent-ink", "accent", 4.5),
                ("accent", "surface", 3.0),
                ("green", "green-dim", 4.5),
                ("red", "red-dim", 4.5),
                ("yellow", "yellow-dim", 4.5),
                ("purple", "purple-dim", 4.5),
                ("accent", "accent-dim", 3.0),
            ):
                with self.subTest(theme=name, pair=f"{fg} on {bg}"):
                    self.assertGreaterEqual(
                        contrast(t[fg], t[bg]), floor, f"{contrast(t[fg], t[bg]):.2f}"
                    )

    def test_invalid_themes_are_rejected(self):
        import tempfile

        good = (ROOT / "themes" / "themes.toml").read_text(encoding="utf-8")
        for bad in (
            good.replace('bg = "#0f1117"', 'bg = "red"', 1),
            good.replace('scheme = "dark"', 'scheme = "blue"', 1),
            good.replace('default_light = "daylight"', 'default_light = "midnight"', 1),
            good.replace('bg = "#0f1117"', 'bg = "#0f1117"\nnope = "#000000"', 1),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "t.toml"
                path.write_text(bad, encoding="utf-8")
                with self.assertRaises(rpt.ThemeError):
                    rpt.load(path)

    def test_rendered_files_are_current(self):
        for name, text in rpt.render(self.document).items():
            self.assertEqual(
                (ROOT / "themes" / name).read_text(encoding="utf-8"), text, name
            )

    def test_script_offers_a_cycling_control_and_every_theme_has_an_icon(self):
        js = rpt.render_js(self.document)
        for needle in ("data-theme-cycle", "PortfolioTheme", "cycle:", "storage"):
            self.assertIn(needle, js)
        for name, theme in self.themes.items():
            self.assertTrue(theme.get("icon"), name)

    def test_chrome_bundle_scopes_tokens_to_the_bar_and_includes_the_bar(self):
        chrome = rpt.render(self.document)["portfolio-chrome.js"]
        for name in self.themes:
            self.assertIn(f'html[data-theme=\\"{name}\\"] .pf-topbar', chrome)
        self.assertIn("--pf-surface:", chrome)
        self.assertNotIn(":root", chrome.split("window.PortfolioTheme")[0])
        for needle in (
            "pf-topbar",
            "/_portfolio/nav.json",
            "PortfolioTheme.bind",
            "data-theme-cycle",
        ):
            self.assertIn(needle, chrome)
        self.assertIn("if (!window.PortfolioTheme)", chrome)

    def test_topbar_is_placed_outside_the_page_body(self):
        js = (ROOT / "themes" / "topbar.js").read_text(encoding="utf-8")
        self.assertIn("document.documentElement.insertBefore(bar, document.body)", js)

    def test_topbar_css_isolates_itself_from_page_styles_first(self):
        css = (ROOT / "themes" / "topbar.css").read_text(encoding="utf-8")
        reset = css.index(".pf-topbar { all: initial; }")
        self.assertLess(reset, css.index(".pf-topbar {\n"))

    def test_topbar_css_uses_only_scoped_tokens(self):
        css = (ROOT / "themes" / "topbar.css").read_text(encoding="utf-8")
        import re

        self.assertEqual(re.findall(r"var\(--(?!pf-)[a-z0-9-]+\)", css), [])
        self.assertEqual(re.findall(r"(?<![&\w])#[0-9a-fA-F]{3,6}\b", css), [])

    def test_new_themes_exist_and_the_old_contrast_name_still_resolves(self):
        for name in ("contrast-dark", "contrast-light", "liberty"):
            self.assertIn(name, self.themes)
        self.assertNotIn("contrast", self.themes)
        self.assertEqual(self.document["meta"]["aliases"]["contrast"], "contrast-dark")
        js = rpt.render_js(self.document)
        self.assertIn('"contrast": "contrast-dark"', js)
        self.assertIn("known(", js)

    def test_stripe_is_optional_validated_and_emitted_for_every_theme(self):
        self.assertIn("stripe", self.themes["liberty"])
        css = rpt.render_css(self.document)
        self.assertIn("--stripe: transparent;", css)
        self.assertIn("--stripe: linear-gradient(", css)
        chrome = rpt.render(self.document)["portfolio-chrome.js"]
        self.assertIn("--pf-stripe:", chrome)
        import tempfile

        good = (ROOT / "themes" / "themes.toml").read_text(encoding="utf-8")
        line = next(ln for ln in good.splitlines() if ln.startswith("stripe = "))
        for bad in (
            'stripe = "red; } body { display: none"',
            'stripe = "url(https://example.test/x)"',
        ):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "t.toml"
                path.write_text(good.replace(line, bad, 1), encoding="utf-8")
                with self.assertRaises(rpt.ThemeError, msg=bad):
                    rpt.load(path)

    def test_script_builds_a_drop_down_menu_of_every_theme(self):
        js = rpt.render_js(self.document)
        for needle in (
            "data-theme-menu",
            "menuitemradio",
            "data-theme-choice",
            "fill: fill",
            "swatch",
        ):
            self.assertIn(needle, js)
        chrome = rpt.render(self.document)["portfolio-chrome.js"]
        self.assertIn("pf-topbar__group--theme", chrome)

    def test_type_is_a_fixed_list_and_the_switcher_overrides_it(self):
        css = rpt.render_css(self.document)
        self.assertIn("--font:", css)
        self.assertIn("--mono:", css)
        self.assertIn("--font-size: 14px;", css)
        self.assertIn('html[data-font="serif"]', css)
        self.assertIn('html[data-size="tiny"] { zoom: 0.8; }', css)
        self.assertIn('html[data-size="large"] { zoom: 1.25; }', css)
        self.assertIn(
            "html { font-family: var(--font); font-size: var(--font-size); zoom: 1; }",
            css,
        )
        js = rpt.render_js(self.document)
        for needle in (
            "portfolio-font",
            "portfolio-size",
            "data-type-menu",
            "data-type-choice",
            "fillType",
        ):
            self.assertIn(needle, js)
        self.assertIn('"Tiny"', js)
        self.assertIn("bindType", js)
        self.assertIn('kind === "size"', js)
        chrome = rpt.render(self.document)["portfolio-chrome.js"]
        self.assertIn('rotatorFor("font"', chrome)
        self.assertIn('rotatorFor("size"', chrome)
        self.assertIn("Choose a font", chrome)
        self.assertIn("Choose a size", chrome)
        with self.assertRaises(rpt.ThemeError):
            rpt.type_settings({"type": {"font": "comic sans", "size": "medium"}})
        with self.assertRaises(rpt.ThemeError):
            rpt.type_settings({"type": {"font": "system", "size": 14}})
        self.assertIn(
            "data-type-menu", rpt.render(self.document)["portfolio-chrome.js"]
        )

    def test_css_defines_every_token_for_every_theme(self):
        css = rpt.render_css(self.document)
        for name in self.themes:
            block = css.split(f':root[data-theme="{name}"] {{')[1].split("}")[0]
            for token in rpt.TOKENS:
                self.assertIn(f"--{token}:", block, f"{name} {token}")
        self.assertIn("prefers-color-scheme: light", css)


if __name__ == "__main__":
    unittest.main()
