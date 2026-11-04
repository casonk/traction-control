"""The shared DNS-rebinding guard in portfolio_site and its use by the global theme writer."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "themes"))
sys.path.insert(0, str(ROOT / "scripts"))
import portfolio_site as seam  # noqa: E402



class HostNameTests(unittest.TestCase):
    def test_extracts_a_lowercase_hostname(self):
        cases = {
            "localhost": "localhost",
            "LocalHost:5100": "localhost",
            "127.0.0.1:5100": "127.0.0.1",
            "[::1]:5100": "::1",
            "[::1]": "::1",
            "::1": "::1",
            "Edge.Example.:443": "edge.example",
            " a.b ": "a.b",
            "a, b": "a",
            "": "",
            None: "",
            "[::1": "",
            ":5100": "",
        }
        for value, expected in cases.items():
            self.assertEqual(seam.host_name(value), expected, value)

    def test_loopback_names(self):
        for name in ("localhost", "127.0.0.1", "127.9.9.9", "::1"):
            self.assertTrue(seam.is_loopback_name(name), name)
        for name in ("", "0.0.0.0", "::", "10.0.0.1", "example.com", "localhost.evil.example", "127.0.0.1.evil.example"):
            self.assertFalse(seam.is_loopback_name(name), name)


class HostAllowedTests(unittest.TestCase):
    allowed = ("site.internal",)

    def test_loopback_and_listed_names_pass(self):
        for host in ("127.0.0.1:5100", "localhost", "[::1]:80", "site.internal", "Site.Internal:8443", "site.internal."):
            self.assertTrue(seam.host_allowed(host, self.allowed), host)

    def test_everything_else_is_refused(self):
        for host in (
            "evil.example",
            "evil.example:5100",
            "site.internal.evil.example",
            "evil-site.internal",
            "localhost.evil.example",
            "192.168.1.5",
            "0.0.0.0:5100",
            "",
            None,
            "evil.example, localhost",
        ):
            self.assertFalse(seam.host_allowed(host, self.allowed), host)

    def test_a_list_given_with_odd_case_or_ports_still_matches(self):
        self.assertTrue(seam.host_allowed("a.internal", ["A.Internal:1"]))

    def test_nothing_listed_means_loopback_only(self):
        self.assertTrue(seam.host_allowed("localhost"))
        self.assertFalse(seam.host_allowed("site.internal"))


def make_site(settings: dict) -> seam.Site:
    return seam.Site(Path("."), None, "none", settings)


class SiteTests(unittest.TestCase):
    def test_a_site_answers_to_its_registered_hostname(self):
        site = make_site({"hostname": "Site.Internal"})
        self.assertTrue(site.host_allowed("site.internal:443", env={}))
        self.assertTrue(site.host_allowed("127.0.0.1:5100", env={}))
        self.assertFalse(site.host_allowed("other.internal", env={}))
        self.assertFalse(site.host_allowed("evil.example", env={}))

    def test_names_come_from_settings_environment_and_extra(self):
        site = make_site({"hostname": "a.internal", "allowed_hosts": ["b.internal", 5, "c.internal:1"]})
        names = site.allowed_hosts(extra=["d.internal"], env={seam.ALLOWED_HOSTS_ENV: "e.internal, ,F.internal:2"})
        self.assertEqual(names, {"a.internal", "b.internal", "c.internal", "d.internal", "e.internal", "f.internal"})

    def test_a_single_string_is_one_name_and_bad_shapes_add_nothing(self):
        self.assertEqual(make_site({"allowed_hosts": "Only.Internal"}).allowed_hosts(env={}), {"only.internal"})
        for bad in (5, {"x": 1}, None, [1, 2]):
            self.assertEqual(make_site({"allowed_hosts": bad}).allowed_hosts(env={}), frozenset())
        self.assertEqual(make_site({}).allowed_hosts(env={}), frozenset())

    def test_the_environment_is_read_by_default(self):
        with mock.patch.dict("os.environ", {seam.ALLOWED_HOSTS_ENV: "env.internal"}):
            self.assertIn("env.internal", make_site({}).allowed_hosts())

    def test_resolve_picks_up_the_store_entrys_hostname(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            entry = root / "store" / "owner__repo"
            entry.mkdir(parents=True)
            (entry / "site.local.toml").write_text('[site]\nhostname = "repo.internal"\nport = 1\n')
            site = seam.resolve(root / "repo", env={seam.SITE_ENV: str(entry), seam.STORE_ENV: str(root / "store")})
            self.assertTrue(site.host_allowed("repo.internal"))
            self.assertFalse(site.host_allowed("evil.example"))


class StoreAndRegistryTests(unittest.TestCase):
    def test_store_hostnames_cover_every_entry_but_not_shared_or_files(self):
        with tempfile.TemporaryDirectory() as raw:
            store = Path(raw)
            for name, text in {
                "a__one": '[site]\nhostname = "one.internal"\n',
                "b__two": '[site]\nhostname = "Two.Internal"\nallowed_hosts = ["alias.internal"]\n',
                "c__bad": "not = = toml",
                "d__none": "[site]\nport = 1\n",
                "_shared": '[site]\nhostname = "shared.internal"\n',
            }.items():
                (store / name).mkdir()
                (store / name / "site.local.toml").write_text(text)
            (store / "stray.txt").write_text("x")
            self.assertEqual(seam.store_hostnames(store), {"one.internal", "two.internal", "alias.internal"})

    def test_a_missing_store_is_empty(self):
        self.assertEqual(seam.store_hostnames(Path("/nonexistent-store-xyz")), frozenset())

    def test_registry_hostnames_merge_both_files(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw) / "util-repos" / "wiring-harness"
            base.mkdir(parents=True)
            (base / "services.toml").write_text('[[services]]\nhostname = "tracked.internal"\n[[services]]\nport = 1\n')
            (base / "services.local.toml").write_text('[[services]]\nhostname = "Local.Internal"\n')
            self.assertEqual(seam.registry_hostnames(raw), {"tracked.internal", "local.internal"})

    def test_a_missing_or_malformed_registry_is_empty(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(seam.registry_hostnames(raw), frozenset())
            base = Path(raw) / "util-repos" / "wiring-harness"
            base.mkdir(parents=True)
            (base / "services.toml").write_text("services = 5\n")
            (base / "services.local.toml").write_text("not = = toml")
            self.assertEqual(seam.registry_hostnames(raw), frozenset())


class ThemeWriterTests(unittest.TestCase):
    base = {"X-Portfolio-Global": "1"}

    def post(self, host, origin="same", **extra):
        headers = {**self.base, "Host": host, **extra}
        if origin == "same":
            headers["Origin"] = f"https://{host}"
        elif origin:
            headers["Origin"] = origin
        return self.writer.handle(headers, b'{"theme": "midnight"}')

    def setUp(self):
        import portfolio_theme_writer  # imported late so the other theme tests bind the same modules

        self.writer = portfolio_theme_writer
        patcher = mock.patch.object(self.writer, "allowed_hosts", return_value=frozenset({"site.internal"}))
        patcher.start()
        self.addCleanup(patcher.stop)
        setter = mock.patch.object(self.writer.pt, "set_global")
        self.set_global = setter.start()
        self.addCleanup(setter.stop)

    def test_a_fronted_site_can_set_the_theme(self):
        status, payload = self.post("site.internal")
        self.assertEqual(int(status), 200)
        self.set_global.assert_called_once_with("midnight")

    def test_loopback_can_set_the_theme(self):
        self.assertEqual(int(self.post("127.0.0.1:8767", origin="http://127.0.0.1:8767")[0]), 200)

    def test_a_rebinding_page_is_refused_even_though_origin_matches_host(self):
        status, _ = self.post("evil.example")
        self.assertEqual(int(status), 403)
        self.set_global.assert_not_called()

    def test_an_unlisted_site_name_and_lookalikes_are_refused(self):
        for host in ("other.internal", "site.internal.evil.example", ""):
            self.assertEqual(int(self.post(host, origin=None)[0]), 403, host)
        self.set_global.assert_not_called()

    def test_the_custom_header_and_origin_rules_still_apply(self):
        headers = {"Host": "site.internal", "Origin": "https://site.internal"}
        self.assertEqual(int(self.writer.handle(headers, b"{}")[0]), 403)
        self.assertEqual(int(self.post("site.internal", origin="https://evil.example")[0]), 403)
        self.set_global.assert_not_called()


class ThemeWriterStartupTests(unittest.TestCase):
    def test_the_script_starts_as_a_program_from_any_directory(self):
        """systemd runs it by path; the test modules put the right folders on sys.path for us."""
        import subprocess

        with tempfile.TemporaryDirectory() as elsewhere:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "portfolio_theme_writer.py"), "--help"],
                cwd=elsewhere,
                capture_output=True,
                text=True,
                timeout=30,
                env={"PATH": "/usr/bin:/bin", "HOME": elsewhere},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--port", result.stdout)


class ThemeWriterAllowedHostsTests(unittest.TestCase):
    def setUp(self):
        import portfolio_theme_writer

        self.writer = portfolio_theme_writer

    def test_union_of_sources(self):
        with (
            mock.patch.object(self.writer.seam, "registry_hostnames", return_value=frozenset({"reg.internal"})),
            mock.patch.object(self.writer.seam, "store_hostnames", return_value=frozenset({"store.internal"})),
            mock.patch.dict("os.environ", {self.writer.seam.ALLOWED_HOSTS_ENV: "env.internal"}),
        ):
            self.assertEqual(
                self.writer.allowed_hosts(), {"reg.internal", "store.internal", "env.internal"}
            )

    def test_host_ok_uses_that_union(self):
        with mock.patch.object(self.writer, "allowed_hosts", return_value=frozenset({"x.internal"})):
            self.assertTrue(self.writer.host_ok({"Host": "x.internal"}))
            self.assertTrue(self.writer.host_ok({"Host": "localhost:8767"}))
            self.assertFalse(self.writer.host_ok({"Host": "evil.example"}))
            self.assertFalse(self.writer.host_ok({}))


if __name__ == "__main__":
    unittest.main()
