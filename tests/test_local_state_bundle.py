import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import local_state_bundle as lsb  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, env=GIT_ENV)


def make_repo(root: Path, rel: str, ignored: dict[str, str], tracked: dict[str, str] | None = None) -> Path:
    repo = root / rel
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text("*.local.*\n*.local\n.env\nsecrets/\nbuild/\n__pycache__/\n")
    for name, text in {**(tracked or {}), ".gitignore": (repo / ".gitignore").read_text()}.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    for name, text in ignored.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    return repo


class Fixture(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name, "portfolio")
        self.home = Path(self._t.name, "home")
        self.home.mkdir()
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh" / "id_test").write_text("PRIVATE")
        (self.home / ".ssh" / "S.agent").write_text("socket-like")
        make_repo(
            self.root, "util-repos/alpha",
            {"config/a.env.local": "A=1", "config/b.local.toml": "b=2", "secrets/k.txt": "secret", "build/x.local.o": "junk",
             "notes.txt": "ignored but not local config"},
            {"config/a.env.example": "A=", "config/c.local.toml.example": "c="},
        )

    def tearDown(self):
        self._t.cleanup()


class Discover(Fixture):
    def test_finds_local_files_and_skips_examples_and_build_output(self):
        files, _ = lsb.discover(self.root, self.home)
        names = set(files)
        self.assertIn("portfolio/util-repos/alpha/config/a.env.local", names)
        self.assertIn("portfolio/util-repos/alpha/config/b.local.toml", names)
        self.assertIn("portfolio/util-repos/alpha/secrets/k.txt", names)
        self.assertIn("home/.ssh/id_test", names)
        self.assertNotIn("portfolio/util-repos/alpha/notes.txt", names)
        self.assertNotIn("portfolio/util-repos/alpha/config/a.env.example", names)
        self.assertFalse([n for n in names if "build" in n or "S.agent" in n])

    def test_oversize_file_is_reported_not_bundled(self):
        (self.root / "util-repos/alpha/config/big.local.json").write_bytes(b"x" * (lsb.MAX_FILE_BYTES + 1))
        files, skipped = lsb.discover(self.root, self.home)
        self.assertNotIn("portfolio/util-repos/alpha/config/big.local.json", files)
        self.assertTrue(any("big.local.json" in s for s in skipped))


    def test_unreadable_file_is_reported_not_fatal(self):
        locked = self.root / "util-repos/alpha/config/locked.local.toml"
        locked.write_text("x")
        with mock.patch.object(lsb.os, "access", side_effect=lambda p, m: Path(p) != locked):
            files, skipped = lsb.discover(self.root, self.home)
        self.assertNotIn("portfolio/util-repos/alpha/config/locked.local.toml", files)
        self.assertTrue(any("locked.local.toml" in s and "not readable" in s for s in skipped))


class BuildRestore(Fixture):
    def test_build_is_deterministic(self):
        files, _ = lsb.discover(self.root, self.home)
        a, b = Path(self._t.name, "a.tgz"), Path(self._t.name, "b.tgz")
        lsb.build_archive(files, a)
        time.sleep(1.1)
        lsb.build_archive(files, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(oct(a.stat().st_mode & 0o777), "0o600")

    def test_restore_round_trip_and_never_overwrites(self):
        files, _ = lsb.discover(self.root, self.home)
        arc = Path(self._t.name, "a.tgz")
        lsb.build_archive(files, arc)
        new_root, new_home = Path(self._t.name, "r2"), Path(self._t.name, "h2")
        dry = lsb.restore(arc, new_root, new_home, apply=False, force=False)
        self.assertTrue(all(line.startswith("would") for line in dry))
        self.assertFalse(new_root.exists())
        lsb.restore(arc, new_root, new_home, apply=True, force=False)
        self.assertEqual((new_root / "util-repos/alpha/config/a.env.local").read_text(), "A=1")
        self.assertEqual(oct((new_home / ".ssh/id_test").stat().st_mode & 0o777), "0o644")
        (new_home / ".ssh/id_test").write_text("CHANGED")
        again = lsb.restore(arc, new_root, new_home, apply=True, force=False)
        self.assertTrue(any(line.startswith("EXISTS") for line in again))
        self.assertEqual((new_home / ".ssh/id_test").read_text(), "CHANGED")
        lsb.restore(arc, new_root, new_home, apply=True, force=True)
        self.assertEqual((new_home / ".ssh/id_test").read_text(), "PRIVATE")

    def test_refuses_traversal_links_and_tampering(self):
        def archive(members):
            p = Path(self._t.name, "evil.tgz")
            with tarfile.open(p, "w:gz") as tar:
                for name, data in members:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            return p

        manifest = json.dumps({"version": 1, "files": {}}).encode()
        with self.assertRaises(lsb.BundleError):
            lsb.read_archive(archive([("MANIFEST.json", manifest), ("../escape", b"x")]))
        with self.assertRaises(lsb.BundleError):
            lsb.read_archive(archive([("MANIFEST.json", manifest), ("/abs", b"x")]))
        with self.assertRaises(lsb.BundleError):  # a member the manifest does not list
            lsb.read_archive(archive([("MANIFEST.json", manifest), ("home/x", b"x")]))
        bad = json.dumps({"version": 1, "files": {"home/x": {"sha256": "0" * 64, "bytes": 1, "mode": "0600"}}}).encode()
        with self.assertRaises(lsb.BundleError):
            lsb.read_archive(archive([("MANIFEST.json", bad), ("home/x", b"x")]))
        link = Path(self._t.name, "link.tgz")
        with tarfile.open(link, "w:gz") as tar:
            info = tarfile.TarInfo("home/l")
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(info)
        with self.assertRaises(lsb.BundleError):
            lsb.read_archive(link)


class Backup(Fixture):
    """The whole backup against a fake store that 'encrypts' by copying, and a local bare remote."""

    def setUp(self):
        super().setUp()
        base = Path(self._t.name)
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.gb = base / "store"
        self.gb.mkdir()
        git(self.gb, "init", "-q", "-b", "main")
        (self.gb / "scripts").mkdir()
        (self.gb / "scripts" / "store.py").write_text(
            "import shutil, sys, pathlib, os\n"
            "src = pathlib.Path(os.path.expanduser(os.environ['STATE_BUNDLE']))\n"
            "out = pathlib.Path('payloads/global/local-state-bundle.age'); out.parent.mkdir(parents=True, exist_ok=True)\n"
            "shutil.copy(src, out)\n"
        )
        (self.gb / "README").write_text("x")
        git(self.gb, "add", "-A")
        git(self.gb, "commit", "-q", "-m", "init")
        git(self.gb, "remote", "add", "origin", str(self.remote))
        git(self.gb, "push", "-q", "-u", "origin", "main")
        self.state = base / "state"
        self.patches = [
            mock.patch.object(lsb, "STATE_DIR", self.state / "local-state"),
            mock.patch.dict(os.environ, {"STATE_BUNDLE": str(self.state / "local-state" / lsb.BUNDLE_NAME), **GIT_ENV}),
        ]
        for p in self.patches:
            p.start()
        self.worktree = self.state / "store-state"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def run_backup(self, **kw):
        return lsb.backup(self.root, self.home, self.gb, self.worktree, "scripts/store.py", force=kw.get("force", False), push=True)

    def test_backs_up_then_skips_unchanged_then_backs_up_a_change(self):
        first = self.run_backup()
        self.assertEqual(first["status"], "backed-up")
        self.assertTrue(first["pushed"])
        branches = git(self.remote, "branch", "--list").stdout
        self.assertIn(lsb.BRANCH, branches)
        self.assertEqual(self.run_backup()["status"], "unchanged")
        (self.root / "util-repos/alpha/config/a.env.local").write_text("A=changed")
        self.assertEqual(self.run_backup()["status"], "backed-up")
        log = git(self.remote, "log", lsb.BRANCH, "--format=%s").stdout.splitlines()
        self.assertEqual(sum("back up local state bundle" in line for line in log), 2)
        self.assertEqual(lsb.status_exit(1.0), 0)

    def test_status_fails_when_stale_or_never_run(self):
        self.assertEqual(lsb.status_exit(1.0), 1)
        lsb.write_state({"last_success": int(time.time()) - 5 * 86400})
        self.assertEqual(lsb.status_exit(2.0), 1)
        self.assertEqual(lsb.status_exit(None), 0)

    def test_failed_capture_is_an_error_and_records_no_success(self):
        (self.gb / "scripts" / "store.py").write_text("import sys; sys.exit(3)\n")
        git(self.gb, "commit", "-qam", "break")
        git(self.gb, "push", "-q", "origin", "main")
        with self.assertRaises(lsb.BundleError):
            self.run_backup()
        self.assertNotIn("last_success", lsb.read_state())


if __name__ == "__main__":
    unittest.main()
