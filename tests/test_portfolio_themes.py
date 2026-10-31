"""Your own portfolio themes: validation, merging, owner-only publishing, symlinks."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("portfolio_themes", ROOT / "scripts" / "portfolio_themes.py")
pt = importlib.util.module_from_spec(spec)
sys.modules["portfolio_themes"] = pt
spec.loader.exec_module(pt)
base = pt.base


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.local = self.root / "themes.local.toml"
        self.rendered = self.root / "themes" / "rendered"
        self.shared = self.root / "sites" / "_shared"
        self.shared.mkdir(parents=True)

    def apply(self, desired):
        return pt.apply(desired, self.local, self.rendered, self.shared)

    def editor_state(self):
        return pt.state(self.local)


class ValidationTests(Case):
    def clean(self, **raw):
        return pt.clean(raw)

    def test_a_good_layer_round_trips_through_toml(self):
        model = self.clean(
            active={"themes": ["midnight", "daylight"]},
            palette={"brand": "#1F6FEB"},
            emoji={"rocket": "🚀"},
            override={"midnight": {"label": "Night", "accent": "#ff8800", "icon": "🌃"}},
            themes={"mine": {"based_on": "ember", "label": "Mine", "scheme": "dark", "bg": "#101010"}},
        )
        self.local.write_text(pt.dump_local(model), encoding="utf-8")
        self.assertEqual(pt.load_local(self.local), model)
        self.assertEqual(model["palette"]["brand"], "#1f6feb")  # colours are stored lowercase

    def test_markup_and_comment_markers_are_refused_in_text(self):
        for bad in ("a<b", 'a"b', "a*/b", "a/*b", "a\\b", "a\nb", "x" * 40, "", "a`b"):
            with self.assertRaises(pt.LocalThemeError, msg=repr(bad)):
                self.clean(override={"midnight": {"label": bad}})
        with self.assertRaises(pt.LocalThemeError):
            self.clean(override={"midnight": {"description": "ok */ body{display:none}"}})

    def test_colours_stripes_icons_and_names_are_checked(self):
        for raw in (
            {"override": {"midnight": {"accent": "red"}}},
            {"override": {"midnight": {"accent": "#12345"}}},
            {"override": {"midnight": {"stripe": "url(http://evil/x)"}}},
            {"override": {"midnight": {"icon": "x"}}},  # ASCII is not an emoji
            {"override": {"midnight": {"icon": "🚀" * 9}}},
            {"override": {"midnight": {"scheme": "sepia"}}},
            {"override": {"midnight": {"nope": "1"}}},
            {"palette": {"Bad Name": "#000000"}},
            {"palette": {"ok": "#00000g"}},
            {"emoji": {"ok": "<b>"}},
            {"themes": {"Bad": {"based_on": "ember", "label": "x"}}},
            {"unexpected": {}},
            {"active": "midnight"},
        ):
            with self.assertRaises(pt.LocalThemeError, msg=str(raw)):
                pt.clean(raw)

    def test_the_libraries_and_theme_count_are_capped(self):
        with self.assertRaises(pt.LocalThemeError):
            self.clean(palette={f"c{i}": "#000000" for i in range(pt.MAX_LIBRARY + 1)})
        with self.assertRaises(pt.LocalThemeError):
            self.clean(themes={f"t{i}": {"based_on": "ember", "label": "x"} for i in range(pt.MAX_THEMES + 1)})

    def test_a_broken_toml_file_names_the_file(self):
        self.local.write_text("[active\n")
        with self.assertRaises(pt.LocalThemeError) as ctx:
            pt.load_local(self.local)
        self.assertIn("themes.local.toml", str(ctx.exception))


class MergeTests(Case):
    def merged(self, **raw):
        return pt.merge(base.load(), pt.clean(raw))

    def test_no_local_layer_is_the_tracked_themes(self):
        self.assertEqual(pt.merge(base.load(), pt.empty()), base.load())

    def test_an_override_changes_only_what_it_names(self):
        doc = self.merged(override={"midnight": {"label": "Night", "accent": "#ff8800"}})
        night, tracked = doc["themes"]["midnight"], base.load()["themes"]["midnight"]
        self.assertEqual((night["label"], night["accent"]), ("Night", "#ff8800"))
        self.assertEqual(night["bg"], tracked["bg"])

    def test_your_theme_starts_from_a_tracked_one_and_needs_a_label(self):
        doc = self.merged(themes={"mine": {"based_on": "ember", "label": "Mine", "accent": "#00ff99"}})
        mine = doc["themes"]["mine"]
        self.assertEqual((mine["accent"], mine["bg"]), ("#00ff99", base.load()["themes"]["ember"]["bg"]))
        with self.assertRaises(pt.LocalThemeError):
            self.merged(themes={"mine": {"based_on": "ember"}})
        with self.assertRaises(pt.LocalThemeError):
            self.merged(themes={"mine": {"based_on": "no-such", "label": "x"}})

    def test_a_tracked_name_cannot_be_redefined_and_unknown_overrides_are_refused(self):
        with self.assertRaises(pt.LocalThemeError):
            self.merged(themes={"midnight": {"based_on": "ember", "label": "x"}})
        with self.assertRaises(pt.LocalThemeError):
            self.merged(override={"no-such": {"label": "x"}})

    def test_active_orders_and_limits_the_menu(self):
        doc = self.merged(active=["neon", "midnight", "daylight"])
        self.assertEqual(list(doc["themes"]), ["neon", "midnight", "daylight"])
        with self.assertRaises(pt.LocalThemeError):
            self.merged(active=["midnight", "ghost"])

    def test_the_defaults_follow_when_the_default_theme_is_switched_off(self):
        doc = self.merged(active=["ember", "contrast-light"])
        self.assertEqual((doc["meta"]["default_dark"], doc["meta"]["default_light"]), ("ember", "contrast-light"))
        self.assertNotIn("contrast", doc["meta"]["aliases"])  # its target is no longer offered
        with self.assertRaises(pt.LocalThemeError):
            self.merged(active=["ember", "midnight"])  # no light theme left

    def test_the_merged_document_renders(self):
        doc = self.merged(
            override={"midnight": {"label": "Night"}},
            themes={"mine": {"based_on": "ember", "label": "Mine", "icon": "🚀"}},
        )
        css, js = base.render_css(doc), base.render_js(doc)
        self.assertIn('data-theme="mine"', css)
        self.assertIn("Night", js)
        self.assertIn("\\ud83d\\ude80", js)  # the icon, as the JSON escape the renderer writes


class PublishTests(Case):
    def test_apply_writes_owner_only_files_and_links_the_store_to_one_render(self):
        state = self.editor_state()
        mid = next(t for t in state["themes"] if t["name"] == "midnight")
        mid["tokens"]["accent"] = "#ff8800"
        result = self.apply({"themes": state["themes"], "active": state["active"]})
        self.assertEqual(sorted(result["linked"]), ["chrome.js", "theme.css", "theme.js"])
        self.assertEqual(stat.S_IMODE(self.local.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.rendered.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.rendered / "theme.css").stat().st_mode), 0o600)
        for name in ("theme.css", "theme.js", "chrome.js"):
            link = self.shared / name
            self.assertTrue(link.is_symlink())
            self.assertFalse(os.path.isabs(os.readlink(link)))  # relative: the config tree can move
            self.assertEqual(link.resolve(), (self.rendered / name).resolve())
        self.assertIn("#ff8800", (self.shared / "theme.css").read_text())
        self.assertEqual(pt.problems(self.local, self.rendered, self.shared), [])

    def test_a_tracked_theme_sent_back_unchanged_is_not_frozen_as_an_override(self):
        state = self.editor_state()
        self.apply({"themes": state["themes"], "active": state["active"]})
        self.assertEqual(pt.load_local(self.local)["override"], {})

    def test_an_existing_copy_is_replaced_by_a_link_atomically_and_relinking_is_a_no_op(self):
        (self.shared / "theme.css").write_text("an old copy")
        state = self.editor_state()
        self.apply({"themes": state["themes"], "active": state["active"]})
        self.assertTrue((self.shared / "theme.css").is_symlink())
        self.assertEqual(pt.link_shared(self.rendered, self.shared), [])
        self.assertFalse(list(self.shared.glob(".*.link")))  # no temporary links left behind

    def test_nothing_changes_when_the_state_is_invalid(self):
        state = self.editor_state()
        self.apply({"themes": state["themes"], "active": state["active"]})
        before = self.local.read_text()
        bad = json.loads(json.dumps(state["themes"]))
        bad[0]["label"] = "<script>"
        with self.assertRaises(pt.LocalThemeError):
            self.apply({"themes": bad, "active": state["active"]})
        self.assertEqual(self.local.read_text(), before)
        with self.assertRaises(pt.LocalThemeError):
            self.apply({"themes": state["themes"], "active": ["midnight", "ember"]})  # no light theme
        self.assertEqual(self.local.read_text(), before)

    def test_your_own_theme_and_the_libraries_survive_a_round_trip(self):
        state = self.editor_state()
        mine = {**state["themes"][0], "name": "mine", "label": "Mine", "icon": "🚀", "based_on": "ember"}
        self.apply(
            {
                "themes": [*state["themes"], mine],
                "active": [*state["active"], "mine"],
                "palette": {"brand": "#1f6feb"},
                "emoji": {"rocket": "🚀"},
            }
        )
        after = self.editor_state()
        names = {t["name"]: t for t in after["themes"]}
        self.assertEqual((names["mine"]["origin"], names["mine"]["active"]), ("custom", True))
        self.assertEqual((after["palette"], after["emoji"]), ({"brand": "#1f6feb"}, {"rocket": "🚀"}))

    def test_switching_a_theme_off_keeps_it_editable(self):
        state = self.editor_state()
        self.apply({"themes": state["themes"], "active": [n for n in state["active"] if n != "hulk"]})
        after = {t["name"]: t for t in self.editor_state()["themes"]}
        self.assertFalse(after["hulk"]["active"])
        self.assertNotIn('data-theme="hulk"', (self.rendered / "theme.css").read_text())

    def test_reset_removes_the_layer_and_turns_the_links_back_into_tracked_copies(self):
        state = self.editor_state()
        self.apply({"themes": state["themes"], "active": ["midnight", "daylight"]})
        result = pt.reset(self.local, self.rendered, self.shared)
        self.assertEqual(sorted(result["restored"]), ["chrome.js", "theme.css", "theme.js"])
        self.assertFalse(self.local.exists())
        css = self.shared / "theme.css"
        self.assertFalse(css.is_symlink())
        self.assertEqual(css.read_text(), base.render(base.load())["portfolio-theme.css"])

    def test_a_stale_render_is_reported(self):
        state = self.editor_state()
        self.apply({"themes": state["themes"], "active": state["active"]})
        (self.rendered / "theme.css").write_text("stale")
        self.assertIn("stale", " ".join(pt.problems(self.local, self.rendered, self.shared)))
        (self.shared / "theme.js").unlink()
        (self.shared / "theme.js").write_text("a copy")
        self.assertIn("not a link", " ".join(pt.problems(self.local, self.rendered, self.shared)))


class CliTests(Case):
    def run_cli(self, *args, stdin=""):
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = pt.main(
                    [*args, "--local", str(self.local), "--rendered", str(self.rendered), "--shared", str(self.shared)]
                )
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def test_show_json_has_what_an_editor_needs(self):
        code, out, _ = self.run_cli("show", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual({t["name"] for t in data["themes"]}, set(base.load()["themes"]))
        self.assertEqual(data["tokens"], list(base.TOKENS))
        self.assertTrue(all(t["origin"] == "builtin" and t["active"] for t in data["themes"]))

    def test_apply_reads_json_on_stdin_and_errors_exit_2_without_changes(self):
        state = json.loads(self.run_cli("show", "--json")[1])
        code, out, _ = self.run_cli("apply", "--stdin", stdin=json.dumps({"themes": state["themes"], "active": state["active"]}))
        self.assertEqual(code, 0)
        self.assertIn("linked", json.loads(out))
        before = self.local.read_text()
        code, _, err = self.run_cli("apply", "--stdin", stdin=json.dumps({"themes": [], "active": ["nope"]}))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        self.assertEqual(self.local.read_text(), before)

    def test_check_passes_after_apply_and_fails_when_the_store_drifts(self):
        state = json.loads(self.run_cli("show", "--json")[1])
        self.run_cli("apply", "--stdin", stdin=json.dumps({"themes": state["themes"], "active": state["active"]}))
        self.assertEqual(self.run_cli("check")[0], 0)
        (self.shared / "theme.css").unlink()
        (self.shared / "theme.css").write_text("copy")
        self.assertEqual(self.run_cli("check")[0], 1)


class TrackedFilesTests(unittest.TestCase):
    def test_the_tracked_render_ignores_any_local_layer(self):
        os.environ["PORTFOLIO_THEMES_LOCAL"] = "/nonexistent/themes.local.toml"
        try:
            self.assertEqual(base.render(base.load())["portfolio-theme.css"], (ROOT / "themes" / "portfolio-theme.css").read_text())
        finally:
            del os.environ["PORTFOLIO_THEMES_LOCAL"]


if __name__ == "__main__":
    unittest.main()
