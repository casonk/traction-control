"""Edges of the portfolio site seam: where the store lives, how a checkout is identified, and
what the fallback server will and will not serve.

This file is vendored, hash-pinned, into every repository, so it is tested here once, at its
source. The seam never raises and never serves anything outside its allowlisted assets, so the
tests pin the refusals as much as the answers.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "themes"))
import portfolio_site as seam  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class StoreLocation(TempCase):
    def test_an_explicit_store_wins_then_xdg_then_the_home_config(self):
        self.assertEqual(seam.default_store({seam.STORE_ENV: "/custom"}), Path("/custom"))
        self.assertEqual(
            seam.default_store({"XDG_CONFIG_HOME": "/xdg"}), Path("/xdg/traction-control/sites")
        )
        with mock.patch.object(Path, "home", return_value=Path("/home/u")):
            self.assertEqual(seam.default_store({}), Path("/home/u/.config/traction-control/sites"))

    def test_an_empty_value_counts_as_unset(self):
        with mock.patch.object(Path, "home", return_value=Path("/home/u")):
            self.assertEqual(
                seam.default_store({seam.STORE_ENV: "", "XDG_CONFIG_HOME": ""}),
                Path("/home/u/.config/traction-control/sites"),
            )

    def test_a_slug_becomes_one_lowercase_directory_name(self):
        self.assertEqual(seam.slug_dirname(" Owner/Repo "), "owner__repo")


class OriginSlug(TempCase):
    def repo(self, name: str, url: str | None) -> Path:
        path = self.root / name
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        if url:
            subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
        return path

    def test_each_github_remote_form_gives_the_same_lowercase_slug(self):
        for url in (
            "git@github.com:Owner/Repo.git",
            "ssh://git@github.com/Owner/Repo",
            "https://github.com/Owner/Repo.git",
            "https://token@github.com/Owner/Repo/",
        ):
            with self.subTest(url=url):
                self.assertEqual(seam.origin_slug(self.repo(f"r{abs(hash(url))}", url)), "owner/repo")

    def test_no_remote_or_a_non_github_remote_or_no_repository_is_none(self):
        self.assertIsNone(seam.origin_slug(self.repo("none", None)))
        self.assertIsNone(seam.origin_slug(self.repo("gitlab", "git@gitlab.com:o/r.git")))
        self.assertIsNone(seam.origin_slug(self.root / "absent"))

    def test_only_the_origin_remote_counts(self):
        path = self.repo("two", "git@github.com:o/origin.git")
        subprocess.run(["git", "-C", str(path), "remote", "add", "other", "git@github.com:o/other.git"], check=True)
        self.assertEqual(seam.origin_slug(path), "o/origin")

    def test_a_linked_worktree_follows_the_shared_config(self):
        main = self.repo("main", "git@github.com:o/shared.git")
        subprocess.run(["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        work = self.root / "linked"
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(work), "-b", "side"], check=True)
        self.assertTrue((work / ".git").is_file())
        self.assertEqual(seam.origin_slug(work), "o/shared")

    def test_a_worktree_pointer_to_a_relative_or_broken_gitdir_is_none_not_an_error(self):
        broken = self.root / "broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: ../nowhere/.git/worktrees/x", encoding="utf-8")
        self.assertIsNone(seam.origin_slug(broken))
        garbage = self.root / "garbage"
        garbage.mkdir()
        (garbage / ".git").write_text("no colon here", encoding="utf-8")
        self.assertIsNone(seam.origin_slug(garbage))


class Reading(TempCase):
    def test_toml_that_is_missing_unreadable_or_unsupported_is_empty(self):
        self.assertEqual(seam._read_toml(self.root / "absent.toml"), {})
        bad = self.root / "bad.toml"
        bad.write_text("[site\n", encoding="utf-8")
        self.assertEqual(seam._read_toml(bad), {})
        good = self.root / "good.toml"
        good.write_text('[site]\nfronting = "direct"\n', encoding="utf-8")
        with mock.patch.object(seam, "tomllib", None):
            self.assertEqual(seam._read_toml(good), {})
        if seam.tomllib is not None:
            self.assertEqual(seam._read_toml(good), {"site": {"fronting": "direct"}})

    def test_a_file_is_served_only_if_it_is_regular_small_and_stays_inside_its_root(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "ok.css").write_bytes(b"body{}")
        outside = self.root / "outside.css"
        outside.write_bytes(b"secret")
        (assets / "link.css").symlink_to(outside)
        (assets / "dir.css").mkdir()
        (assets / "big.css").write_bytes(b"x" * (seam.MAX_ASSET_BYTES + 1))
        (assets / "edge.css").write_bytes(b"x" * seam.MAX_ASSET_BYTES)
        self.assertEqual(seam._read_regular(assets / "ok.css", assets), b"body{}")
        self.assertIsNone(seam._read_regular(assets / "link.css", assets))
        self.assertIsNone(seam._read_regular(assets / "dir.css", assets))
        self.assertIsNone(seam._read_regular(assets / "big.css", assets))
        self.assertEqual(len(seam._read_regular(assets / "edge.css", assets)), seam.MAX_ASSET_BYTES)
        self.assertIsNone(seam._read_regular(assets / "absent.css", assets))
        self.assertIsNone(seam._read_regular(assets / "ok.css", self.root / "absent-root"))

    def test_inside_is_a_path_relationship_not_a_string_prefix(self):
        self.assertTrue(seam._inside(Path("/a/b/c"), Path("/a/b")))
        self.assertTrue(seam._inside(Path("/a/b"), Path("/a/b")))
        self.assertFalse(seam._inside(Path("/a/bc"), Path("/a/b")))
        self.assertFalse(seam._inside(Path("/a"), Path("/a/b")))


class Serving(TempCase):
    def site(self, **kwargs) -> seam.Site:
        defaults = dict(repo_root=self.root, site_dir=None, source="none", settings={"fronting": "direct"})
        return seam.Site(**{**defaults, **kwargs})

    def test_only_allowlisted_names_are_answered(self):
        site = self.site()
        for name in ("secret.txt", "../theme.css", "theme.css/", "THEME.CSS", "", "nav.json.bak", "sub/theme.css"):
            with self.subTest(name=name):
                self.assertIsNone(site.asset(name))

    def test_a_request_outside_the_prefix_is_not_ours_and_query_and_fragment_are_ignored(self):
        vendored = self.root / "v"
        vendored.mkdir()
        (vendored / "theme.css").write_bytes(b"css")
        site = self.site(vendored=vendored)
        self.assertIsNone(site.response("/other/theme.css"))
        self.assertEqual(site.response("/_portfolio/theme.css?v=3#x"), (b"css", "text/css; charset=utf-8"))

    def test_the_site_directory_overrides_the_vendored_copy_and_a_missing_nav_is_empty(self):
        site_dir, vendored = self.root / "s", self.root / "v"
        (site_dir / "assets").mkdir(parents=True)
        vendored.mkdir()
        (site_dir / "assets" / "theme.css").write_bytes(b"site")
        (vendored / "theme.css").write_bytes(b"vendored")
        (vendored / "chrome.js").write_bytes(b"chrome")
        site = self.site(site_dir=site_dir, vendored=vendored)
        self.assertEqual(site.asset("theme.css")[0], b"site")
        self.assertEqual(site.asset("chrome.js")[0], b"chrome")
        self.assertEqual(site.asset("nav.json"), (seam.EMPTY_NAV, "application/json"))
        self.assertIsNone(site.asset("theme.js"))

    def test_describe_reports_where_everything_came_from(self):
        site = self.site(site_dir=self.root / "s", source="env", vendored=self.root / "v")
        self.assertEqual(
            site.describe(),
            {"source": "env", "site_dir": str(self.root / "s"), "fronting": "direct", "vendored": str(self.root / "v")},
        )
        bare = self.site().describe()
        self.assertIsNone(bare["site_dir"])
        self.assertIsNone(bare["vendored"])


class Resolution(TempCase):
    def test_a_site_directory_outside_the_store_is_ignored_unless_named_by_the_environment(self):
        store = self.root / "store"
        store.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".portfolio").symlink_to(outside)
        env = {seam.STORE_ENV: str(store)}
        self.assertEqual(seam.resolve(repo, env=env).source, "none")
        self.assertEqual(seam.resolve(repo, env={**env, seam.SITE_ENV: str(outside)}).source, "env")

    def test_a_link_into_the_store_is_accepted(self):
        store = self.root / "store"
        (store / "o__r").mkdir(parents=True)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".portfolio").symlink_to(store / "o__r")
        self.assertEqual(seam.resolve(repo, env={seam.STORE_ENV: str(store)}).source, "link")

    def test_a_store_that_cannot_be_resolved_falls_through_instead_of_raising(self):
        store = self.root / "store"
        store.mkdir()
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".portfolio").symlink_to(store)
        original = Path.resolve

        def resolve(self, *args, **kwargs):
            if self == store:
                raise OSError("store unreadable")
            return original(self, *args, **kwargs)

        with mock.patch.object(Path, "resolve", resolve):
            self.assertEqual(seam.resolve(repo, env={seam.STORE_ENV: str(store)}).source, "none")

    def test_an_unknown_fronting_setting_falls_back_to_direct(self):
        store = self.root / "store"
        site_dir = store / "o__r"
        site_dir.mkdir(parents=True)
        (site_dir / "site.local.toml").write_text('[site]\nfronting = "bogus"\n', encoding="utf-8")
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".portfolio").symlink_to(site_dir)
        self.assertEqual(seam.resolve(repo, env={seam.STORE_ENV: str(store)}).settings["fronting"], "direct")


if __name__ == "__main__":
    unittest.main()
