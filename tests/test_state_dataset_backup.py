import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import state_dataset_backup as sdb  # noqa: E402

TOOLS = all(shutil.which(t) for t in ("age", "age-keygen", "zstd"))


@unittest.skipUnless(TOOLS, "needs age, age-keygen and zstd")
class Backup(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.base = Path(self._t.name)
        self.src = self.base / "src"
        (self.src / "docs").mkdir(parents=True)
        (self.src / "docs" / "a.txt").write_text("alpha")
        (self.src / "docs" / "skip.tmp").write_text("junk")
        db = self.src / "app.db"
        con = sqlite3.connect(db)
        con.execute("create table t(x)")
        con.execute("insert into t values (42)")
        con.commit()
        con.close()
        self.identity = self.base / "id.txt"
        out = subprocess.run(["age-keygen", "-o", str(self.identity)], capture_output=True, text=True, check=True)
        self.recipient = out.stderr.split("Public key:")[1].strip()
        self.dest = self.base / "dest"
        self.dest.mkdir()
        self.ds = {"name": "demo", "paths": [str(self.src)], "sqlite": [str(db)], "exclude": ["*.tmp"]}

    def tearDown(self):
        self._t.cleanup()

    def run_backup(self, **kw):
        return sdb.backup_dataset(self.ds, self.dest, [self.recipient], self.identity, kw.get("keep", 3),
                                  force=kw.get("force", False), verify=True)

    def test_backup_verifies_and_the_archive_is_ciphertext(self):
        msg = self.run_backup()
        self.assertIn("verified", msg)
        arc = sdb.generations(self.dest, "demo")[0]
        self.assertNotIn(b"alpha", arc.read_bytes())
        meta = json.loads(arc.with_name(arc.name + ".json").read_text())
        self.assertTrue(meta["verified"])
        self.assertEqual(meta["files"], 2)  # a.txt and app.db; skip.tmp excluded
        self.assertNotIn("a.txt", json.dumps(meta))  # metadata carries no file names

    def test_unchanged_is_skipped_then_a_change_makes_a_generation(self):
        self.run_backup()
        self.assertIn("unchanged", self.run_backup())
        time.sleep(1.1)
        (self.src / "docs" / "a.txt").write_text("alpha two")
        self.assertIn("wrote", self.run_backup())
        self.assertEqual(len(sdb.generations(self.dest, "demo")), 2)

    def test_retention_keeps_only_the_newest(self):
        for i in range(4):
            time.sleep(1.1)
            (self.src / "docs" / "a.txt").write_text(f"v{i}")
            self.run_backup(keep=2)
        self.assertEqual(len(sdb.generations(self.dest, "demo")), 2)
        self.assertEqual(len(list(self.dest.glob("*.json"))), 2)

    def test_restore_returns_files_and_a_consistent_sqlite_copy(self):
        self.run_backup()
        target = self.base / "restored"
        cfg = {"dest": str(self.dest), "identity": str(self.identity), "dataset": [self.ds]}
        args = mock.Mock(name="demo", to=str(target), generation=None)
        args.name = "demo"
        self.assertEqual(sdb.cmd_restore(cfg, args), 0)
        root = target / str(self.src).lstrip("/")
        self.assertEqual((root / "docs" / "a.txt").read_text(), "alpha")
        self.assertFalse((root / "docs" / "skip.tmp").exists())
        row = sqlite3.connect(root / "app.db").execute("select x from t").fetchone()
        self.assertEqual(row, (42,))
        with self.assertRaises(sdb.BackupError):  # never into a non-empty directory
            sdb.cmd_restore(cfg, args)

    def test_corruption_is_caught_by_verify(self):
        self.run_backup()
        arc = sdb.generations(self.dest, "demo")[0]
        data = bytearray(arc.read_bytes())
        data[len(data) // 2] ^= 0xFF
        arc.write_bytes(bytes(data))
        with self.assertRaises(sdb.BackupError):
            sdb.verify_archive(arc, self.identity, None)

    def test_failed_verification_removes_the_new_generation(self):
        with mock.patch.object(sdb, "verify_archive", side_effect=sdb.BackupError("boom")):
            with self.assertRaises(sdb.BackupError):
                self.run_backup()
        self.assertEqual(list(self.dest.iterdir()), [])


class Guards(unittest.TestCase):
    def test_flash_destination_is_refused_unless_allowed(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(sdb, "disk_kind", return_value="flash"):
            with self.assertRaises(sdb.BackupError):
                sdb.check_destination(Path(t, "d"), allow_flash=False)
            sdb.check_destination(Path(t, "d"), allow_flash=True)

    def test_unknown_medium_fails_closed_and_hdd_passes(self):
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(sdb, "disk_kind", return_value="unknown"), self.assertRaises(sdb.BackupError):
                sdb.check_destination(Path(t, "d"), allow_flash=False)
            with mock.patch.object(sdb, "disk_kind", return_value="hdd"):
                sdb.check_destination(Path(t, "d"), allow_flash=False)

    def test_destination_inside_a_git_worktree_is_refused(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(sdb, "disk_kind", return_value="hdd"):
            subprocess.run(["git", "init", "-q", t], check=True)
            with self.assertRaises(sdb.BackupError):
                sdb.check_destination(Path(t, "backups"), allow_flash=False)

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t, "c.toml")
            cfg.write_text('[[dataset]]\nname = "Bad Name"\npaths = ["/x"]\n')
            with self.assertRaises(sdb.BackupError):
                sdb.load_config(cfg)
            cfg.write_text('[[dataset]]\nname = "a"\npaths = ["/x"]\n[[dataset]]\nname = "a"\npaths = ["/y"]\n')
            with self.assertRaises(sdb.BackupError):
                sdb.load_config(cfg)
            with self.assertRaises(sdb.BackupError):
                sdb.load_config(Path(t, "missing.toml"))

    def test_status_reports_missing_unverified_and_stale(self):
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t)
            cfg = {"dest": str(dest), "dataset": [{"name": "x", "paths": ["/p"]}]}
            args = mock.Mock(max_age_days=2.0)
            self.assertEqual(sdb.cmd_status(cfg, args), 1)  # no backup
            arc = dest / "x-20260101T000000Z.tar.zst.age"
            arc.write_bytes(b"c")
            arc.with_name(arc.name + ".json").write_text(json.dumps({"created": int(time.time()), "verified": True}))
            self.assertEqual(sdb.cmd_status(cfg, args), 0)
            arc.with_name(arc.name + ".json").write_text(json.dumps({"created": int(time.time()) - 9 * 86400, "verified": True}))
            self.assertEqual(sdb.cmd_status(cfg, args), 1)  # stale


if __name__ == "__main__":
    unittest.main()
