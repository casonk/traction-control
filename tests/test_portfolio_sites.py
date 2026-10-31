"""The portfolio site seam and the enrollment command: resolution, safety, link, vendor, check."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "themes"))
import portfolio_site as seam  # noqa: E402

spec = importlib.util.spec_from_file_location("portfolio_sites", ROOT / "scripts" / "portfolio_sites.py")
ps = importlib.util.module_from_spec(spec)
sys.modules["portfolio_sites"] = ps
spec.loader.exec_module(ps)


def git_repo(path: Path, slug: str, ignore: bool = True) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", f"git@github.com:{slug}.git"], check=True)
    if ignore:
        (path / ".gitignore").write_text("/.portfolio\n")
    return path


class SeamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"
        (self.store / "_shared").mkdir(parents=True)
        for name in ("theme.css", "theme.js", "chrome.js", "nav.json"):
            (self.store / "_shared" / name).write_text(f"shared {name}")
        self.repo = git_repo(self.root / "repo", "Owner/Repo")
        self.vendored = self.repo / "vendored"
        self.vendored.mkdir()
        for name in ("theme.css", "theme.js", "chrome.js"):
            (self.vendored / name).write_text(f"vendored {name}")
        self.env = {seam.STORE_ENV: str(self.store)}

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, dirname="owner__repo", fronting="repo-caddy"):
        d = self.store / dirname
        d.mkdir()
        (d / "assets").symlink_to("../_shared")
        (d / "site.local.toml").write_text(f'[site]\nfronting = "{fronting}"\n')
        return d

    def test_origin_slug_from_ssh_https_and_worktree(self):
        self.assertEqual(seam.origin_slug(self.repo), "owner/repo")
        other = git_repo(self.root / "o2", "A/B")
        subprocess.run(["git", "-C", str(other), "remote", "set-url", "origin", "https://x:y@github.com/A/B.git"], check=True)
        self.assertEqual(seam.origin_slug(other), "a/b")
        self.assertIsNone(seam.origin_slug(self.root))

    def test_link_wins_then_slug_fallback_then_none(self):
        d = self.entry()
        site = seam.resolve(self.repo, vendored=self.vendored, env=self.env)
        self.assertEqual((site.source, site.site_dir), ("slug", d.resolve()))
        (self.repo / ".portfolio").symlink_to(d)
        self.assertEqual(seam.resolve(self.repo, env=self.env).source, "link")
        self.assertEqual(seam.resolve(self.repo, env={**self.env, seam.SITE_ENV: str(d)}).source, "env")
        bare = seam.resolve(self.root / "nowhere", env=self.env)
        self.assertEqual(bare.source, "none")

    def test_a_link_or_slug_entry_outside_the_store_is_ignored(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.repo / ".portfolio").symlink_to(elsewhere)
        self.assertEqual(seam.resolve(self.repo, env=self.env).source, "none")

    def test_settings_layer_defaults_example_then_local(self):
        self.assertEqual(seam.resolve(self.repo, env=self.env).settings["fronting"], "direct")
        (self.repo / "config").mkdir()
        (self.repo / "config" / "portfolio-site.example.toml").write_text('[site]\nfronting = "wiring-harness-caddy"\n')
        self.assertEqual(seam.resolve(self.repo, env=self.env).settings["fronting"], "wiring-harness-caddy")
        self.entry(fronting="repo-caddy")
        self.assertEqual(seam.resolve(self.repo, env=self.env).settings["fronting"], "repo-caddy")
        self.entry_bad = None

    def test_unknown_fronting_falls_back_to_the_default(self):
        d = self.entry()
        (d / "site.local.toml").write_text('[site]\nfronting = "nonsense"\n')
        self.assertEqual(seam.resolve(self.repo, env=self.env).settings["fronting"], "direct")

    def test_assets_come_from_the_store_then_the_vendored_copy_then_nav_falls_back_to_empty(self):
        self.entry()
        site = seam.resolve(self.repo, vendored=self.vendored, env=self.env)
        self.assertEqual(site.response("/_portfolio/theme.css"), (b"shared theme.css", "text/css; charset=utf-8"))
        self.assertEqual(site.response("/_portfolio/theme.js?x=1")[0], b"shared theme.js")
        bare = seam.resolve(self.repo, vendored=self.vendored, env={seam.STORE_ENV: str(self.root / "none")})
        self.assertEqual(bare.response("/_portfolio/theme.css")[0], b"vendored theme.css")
        self.assertEqual(bare.response("/_portfolio/nav.json"), (seam.EMPTY_NAV, "application/json"))
        nothing = seam.resolve(self.root / "x", env=self.env)
        self.assertIsNone(nothing.response("/_portfolio/theme.css"))
        self.assertEqual(nothing.response("/_portfolio/nav.json")[0], seam.EMPTY_NAV)

    def test_only_the_four_names_under_the_prefix_are_served(self):
        self.entry()
        site = seam.resolve(self.repo, vendored=self.vendored, env=self.env)
        for path in ("/_portfolio/site.local.toml", "/_portfolio/../x", "/_portfolio/assets/theme.css", "/other/theme.css", "/_portfolio/"):
            self.assertIsNone(site.response(path), path)

    def test_a_symlinked_asset_that_escapes_its_directory_is_refused(self):
        secret = self.root / "secret.txt"
        secret.write_text("secret")
        d = self.entry()
        shared = self.store / "_shared"
        (shared / "theme.css").unlink()
        (shared / "theme.css").symlink_to(secret)
        site = seam.resolve(self.repo, vendored=self.vendored, env=self.env)
        # the store copy is refused, so the vendored copy answers instead of the secret
        self.assertEqual(site.response("/_portfolio/theme.css")[0], b"vendored theme.css")
        (self.vendored / "theme.css").unlink()
        (self.vendored / "theme.css").symlink_to(secret)
        self.assertIsNone(site.response("/_portfolio/theme.css"))
        self.assertTrue(d.exists())

    def test_oversized_assets_are_refused(self):
        self.entry()
        (self.store / "_shared" / "theme.css").write_bytes(b"x" * (seam.MAX_ASSET_BYTES + 1))
        site = seam.resolve(self.repo, vendored=self.vendored, env=self.env)
        self.assertEqual(site.response("/_portfolio/theme.css")[0], b"vendored theme.css")


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.portfolio = self.root / "portfolio"
        self.store = self.root / "store"
        self.base = self.root / "sites.conf"
        self.overlay = self.root / "overlay.conf"
        self.classes = {"o/pub": "public", "o/priv": "private", "o/unknown": "unclassified"}
        self.classify = lambda slug: self.classes.get(slug, "unclassified")
        # the temp directory may itself be inside a stray work tree; test that rule separately
        self._exposed = ps._exposed_by_git
        ps._exposed_by_git = lambda path: False

    def tearDown(self):
        ps._exposed_by_git = self._exposed
        self.tmp.cleanup()

    def test_an_overlay_in_an_unignored_work_tree_is_refused_and_an_ignored_one_accepted(self):
        ps._exposed_by_git = self._exposed
        tree = git_repo(self.root / "tree", "o/t", ignore=False)
        overlay = tree / "private.conf"
        overlay.write_text("priv|o/priv|util-repos/priv|v|direct\n")
        os.chmod(overlay, 0o600)
        self.base.write_text("")
        with self.assertRaisesRegex(ps.SiteError, "unignored"):
            ps.load_entries(self.base, overlay, self.classify)
        (tree / ".gitignore").write_text("private.conf\n")
        self.assertEqual(len(ps.load_entries(self.base, overlay, self.classify)), 1)

    def test_parse_rejects_bad_lines(self):
        for bad in ("a|o/x|p|v", "A|o/x|p|v|direct", "a|nonsense|p|v|direct", "a|o/x|../p|v|direct", "a|o/x|p|/abs|direct", "a|o/x|p|v|weird"):
            with self.subTest(bad=bad), self.assertRaises(ps.SiteError):
                ps.parse_config(bad, private=False)
        got = ps.parse_config("# c\n\npub|o/pub|util-repos/pub|pkg/portfolio|repo-caddy\n", private=False)
        self.assertEqual((got[0].name, got[0].fronting), ("pub", "repo-caddy"))

    def test_the_tracked_base_may_only_name_public_repositories(self):
        self.base.write_text("priv|o/priv|util-repos/priv|v|direct\n")
        with self.assertRaisesRegex(ps.SiteError, "not public"):
            ps.load_entries(self.base, self.overlay, self.classify)

    def test_the_overlay_is_verified_private_owner_only_and_untracked(self):
        self.base.write_text("pub|o/pub|util-repos/pub|v|direct\n")
        self.overlay.write_text("priv|o/priv|util-repos/priv|v|direct\n")
        os.chmod(self.overlay, 0o644)
        with self.assertRaisesRegex(ps.SiteError, "owner-only"):
            ps.load_entries(self.base, self.overlay, self.classify)
        os.chmod(self.overlay, 0o600)
        entries = ps.load_entries(self.base, self.overlay, self.classify)
        self.assertEqual([e.name for e in entries], ["pub", "priv"])
        self.assertIn("(private)", ps.label(entries[1], show_slugs=False))
        self.assertIn("o/priv", ps.label(entries[1], show_slugs=True))
        for slug, message in (("o/pub", "not private"), ("o/unknown", "not private")):
            self.overlay.write_text(f"x|{slug}|util-repos/x|v|direct\n")
            with self.assertRaisesRegex(ps.SiteError, message):
                ps.load_entries(self.base, self.overlay, self.classify)
        self.overlay.write_text("pub|o/priv|util-repos/priv|v|direct\n")
        with self.assertRaisesRegex(ps.SiteError, "duplicate"):
            ps.load_entries(self.base, self.overlay, self.classify)

    def test_link_plans_first_then_creates_the_store_entry_and_the_checkout_link(self):
        repo = git_repo(self.portfolio / "util-repos" / "pub", "o/pub")
        wh = self.portfolio / "util-repos" / "wiring-harness"
        wh.mkdir(parents=True)
        (wh / "services.toml").write_text(
            '[[services]]\nname = "pub"\nowner_repo = "./util-repos/pub"\nhostname = "pub.example.internal"\n'
            'ingress = "wiring-harness-caddy"\nport = 5100\n'
        )
        entry = ps.parse_config("pub|o/pub|util-repos/pub|pkg/portfolio|direct", private=False)[0]
        plan, problems = ps.link([entry], self.portfolio, self.store, apply=False, show_slugs=False)
        self.assertEqual(problems, [])
        self.assertFalse((repo / ".portfolio").exists())
        ps.link([entry], self.portfolio, self.store, apply=True, show_slugs=False)
        entry_dir = self.store / "o__pub"
        self.assertEqual((repo / ".portfolio").resolve(), entry_dir.resolve())
        self.assertEqual(oct(entry_dir.stat().st_mode & 0o777), "0o700")
        toml = (entry_dir / "site.local.toml")
        self.assertEqual(oct(toml.stat().st_mode & 0o777), "0o600")
        text = toml.read_text()
        self.assertIn('fronting = "wiring-harness-caddy"', text)
        self.assertIn('hostname = "pub.example.internal"', text)
        self.assertIn("port = 5100", text)
        self.assertEqual(os.readlink(entry_dir / "assets"), "../_shared")
        again, problems = ps.link([entry], self.portfolio, self.store, apply=True, show_slugs=False)
        self.assertTrue(again[0].startswith("ok"))

    def test_link_refuses_unsafe_checkouts(self):
        entry = ps.parse_config("pub|o/pub|util-repos/pub|pkg/portfolio|direct", private=False)[0]
        _, problems = ps.link([entry], self.portfolio, self.store, True, False)
        self.assertIn("no checkout", problems[0])
        git_repo(self.portfolio / "util-repos" / "pub", "someone/else")
        _, problems = ps.link([entry], self.portfolio, self.store, True, False)
        self.assertIn("origin is not the enrolled repository", problems[0])
        subprocess.run(["git", "-C", str(self.portfolio / "util-repos" / "pub"), "remote", "set-url", "origin", "git@github.com:o/pub.git"], check=True)
        (self.portfolio / "util-repos" / "pub" / ".gitignore").write_text("")
        _, problems = ps.link([entry], self.portfolio, self.store, True, False)
        self.assertIn(".gitignore", problems[0])
        self.assertFalse((self.store / "o__pub").exists())
        (self.portfolio / "util-repos" / "pub" / ".gitignore").write_text("/.portfolio\n")
        (self.portfolio / "util-repos" / "pub" / ".portfolio").write_text("a real file")
        _, problems = ps.link([entry], self.portfolio, self.store, True, False)
        self.assertIn("not a symlink", problems[0])

    def test_private_slugs_stay_out_of_problem_text_by_default(self):
        entry = ps.parse_config("priv|o/priv|util-repos/priv|v|direct", private=True)[0]
        _, problems = ps.link([entry], self.portfolio, self.store, False, show_slugs=False)
        self.assertNotIn("o/priv", problems[0])

    def test_publish_copies_the_theme_files_only_when_applied_and_is_idempotent(self):
        extra = self.root / "extra"
        notes = ps.publish(self.store, [extra], apply=False)
        self.assertTrue(all(n.startswith("publish") for n in notes))
        self.assertFalse((self.store / "_shared").exists())
        ps.publish(self.store, [extra], apply=True)
        for name, source in ps.PUBLISHED.items():
            self.assertEqual((self.store / "_shared" / name).read_bytes(), (ROOT / "themes" / source).read_bytes())
            self.assertEqual((extra / name).read_bytes(), (ROOT / "themes" / source).read_bytes())
        self.assertTrue(all(n.startswith("ok") for n in ps.publish(self.store, [extra], apply=True)))

    def test_vendor_writes_the_fallback_copy_and_a_manifest_that_matches(self):
        repo = git_repo(self.portfolio / "util-repos" / "pub", "o/pub")
        entry = ps.parse_config("pub|o/pub|util-repos/pub|pkg/portfolio|direct", private=False)[0]
        self.assertEqual(ps.vendor(entry, self.portfolio, apply=False).strip(), "vendor")
        self.assertFalse((repo / "pkg").exists())
        ps.vendor(entry, self.portfolio, apply=True)
        target = repo / "pkg" / "portfolio"
        self.assertEqual(sorted(p.name for p in target.iterdir()), sorted([*ps.VENDORED, ps.MANIFEST]))
        self.assertEqual((target / ps.MANIFEST).read_text(), ps.manifest_text(target))
        self.assertEqual(ps.vendor(entry, self.portfolio, apply=False).strip(), "ok")
        (target / "theme.css").write_text("tampered")
        self.assertEqual(ps.vendor(entry, self.portfolio, apply=False).strip(), "vendor")

    def test_the_vendored_directory_works_as_a_standalone_fallback(self):
        repo = git_repo(self.portfolio / "util-repos" / "pub", "o/pub")
        entry = ps.parse_config("pub|o/pub|util-repos/pub|pkg/portfolio|direct", private=False)[0]
        ps.vendor(entry, self.portfolio, apply=True)
        site = seam.resolve(repo, vendored=repo / "pkg" / "portfolio", env={seam.STORE_ENV: str(self.root / "none")})
        body, ctype = site.response("/_portfolio/chrome.js")
        self.assertEqual(body, (ROOT / "themes" / "portfolio-chrome.js").read_bytes())
        self.assertEqual(site.source, "none")

    def test_check_reports_unlinked_stale_and_unpublished_sites(self):
        git_repo(self.portfolio / "util-repos" / "pub", "o/pub")
        entry = ps.parse_config("pub|o/pub|util-repos/pub|pkg/portfolio|direct", private=False)[0]
        problems = ps.check([entry], self.portfolio, self.store, False)
        joined = "\n".join(problems)
        for needle in ("not linked", "vendored fallback is stale", "theme.css is missing or stale"):
            self.assertIn(needle, joined)
        ps.vendor(entry, self.portfolio, apply=True)
        ps.link([entry], self.portfolio, self.store, True, False)
        ps.publish(self.store, [], True)
        self.assertEqual(ps.check([entry], self.portfolio, self.store, False), [])

    def test_the_tracked_base_config_parses(self):
        entries = ps.parse_config(ps.BASE_CONFIG.read_text(), private=False, origin="sites.conf")
        self.assertGreaterEqual(len(entries), 3)
        self.assertEqual(len({e.name for e in entries}), len(entries))


if __name__ == "__main__":
    unittest.main()
