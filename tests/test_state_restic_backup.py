import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import state_restic_backup as srb  # noqa: E402


class Args:
    def __init__(self, **kw):
        self.allow_flash = False
        self.no_forget = False
        self.names = []
        self.dry_run = False
        self.max_age_days = None
        self.read_data = "100%"
        self.__dict__.update(kw)


@unittest.skipUnless(shutil.which("restic"), "restic is not installed")
class Restic(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.base = Path(self._t.name)
        self.src = self.base / "src"
        (self.src / "docs").mkdir(parents=True)
        (self.src / "docs" / "a.txt").write_text("alpha")
        (self.src / "docs" / "skip.tmp").write_text("junk")
        (self.src / "docs" / "big.bin").write_bytes(__import__("os").urandom(300_000))
        db = self.src / "app.db"
        con = sqlite3.connect(db)
        con.execute("create table t(x)")
        con.execute("insert into t values (7)")
        con.commit()
        con.close()
        self.cfg = {
            "restic": {"repository": str(self.base / "repo"), "password_file": str(self.base / "pw"), "compression": "auto",
                       "keep_daily": 2, "keep_weekly": 1, "keep_monthly": 1, "keep_yearly": 1},
            "dataset": [{"name": "demo", "paths": [str(self.src)], "sqlite": [str(db)], "exclude": ["*.tmp"]}],
        }
        self.patches = [
            mock.patch.object(srb, "STAGE", self.base / "stage"),
            mock.patch.object(srb.sdb, "check_destination", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()
        self.assertEqual(srb.cmd_init(self.cfg, Args()), 0)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._t.cleanup()

    def snaps(self):
        return srb.snapshots(srb.settings(self.cfg))

    def test_init_creates_an_owner_only_password_and_a_v2_repository(self):
        self.assertEqual(oct((self.base / "pw").stat().st_mode & 0o777), "0o600")
        self.assertGreater(len((self.base / "pw").read_text().strip()), 40)
        self.assertEqual(json.loads(srb.restic(srb.settings(self.cfg), "cat", "config").stdout)["version"], 2)
        self.assertEqual(srb.cmd_init(self.cfg, Args()), 0)  # idempotent

    def raw_bytes(self) -> int:
        out = srb.restic(srb.settings(self.cfg), "stats", "--mode", "raw-data", "--json").stdout
        return json.loads(out)["total_size"]

    def test_unchanged_data_adds_nothing_and_a_change_adds_data(self):
        self.assertEqual(srb.cmd_backup(self.cfg, Args(no_forget=True)), 0)
        first = self.raw_bytes()
        self.assertGreaterEqual(len(self.snaps()), 1)
        srb.cmd_backup(self.cfg, Args(no_forget=True))
        self.assertLess(self.raw_bytes() - first, 4096)  # only a small directory-metadata tree, never the 300 KB again
        (self.src / "docs" / "big.bin").write_bytes(__import__("os").urandom(300_000))
        srb.cmd_backup(self.cfg, Args(no_forget=True))
        self.assertGreater(self.raw_bytes() - first, 250_000)

    def test_drill_matches_files_and_integrity_checks_the_database(self):
        srb.cmd_backup(self.cfg, Args())
        self.assertEqual(srb.cmd_drill(self.cfg, Args(name="demo")), 0)
        (self.src / "docs" / "a.txt").write_text("changed after the snapshot")
        self.assertEqual(srb.cmd_drill(self.cfg, Args(name="demo")), 1)  # the live file no longer matches

    def test_restore_returns_files_and_refuses_a_non_empty_target(self):
        srb.cmd_backup(self.cfg, Args())
        out = self.base / "out"
        self.assertEqual(srb.cmd_restore(self.cfg, Args(name="demo", to=str(out))), 0)
        self.assertEqual(next(out.rglob("a.txt")).read_text(), "alpha")
        self.assertFalse(list(out.rglob("skip.tmp")))
        db = next(out.rglob("app.db"))
        self.assertEqual(sqlite3.connect(db).execute("select x from t").fetchone(), (7,))
        with self.assertRaises(srb.ResticError):
            srb.cmd_restore(self.cfg, Args(name="demo", to=str(out)))

    def test_check_passes_and_status_reports_age(self):
        srb.cmd_backup(self.cfg, Args())
        self.assertEqual(srb.cmd_check(self.cfg, Args()), 0)
        self.assertEqual(srb.cmd_status(self.cfg, Args(max_age_days=1.0)), 0)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            srb.cmd_status({**self.cfg, "dataset": self.cfg["dataset"][:1]}, Args())
        age = float(buf.getvalue().split("newest ")[1].split(" days")[0])
        self.assertLess(abs(age), 0.01)  # a snapshot made seconds ago is not hours old, whatever the timezone
        self.cfg["dataset"].append({"name": "never", "paths": ["/nope"]})
        self.assertEqual(srb.cmd_status(self.cfg, Args()), 1)

    def test_a_changed_database_is_restaged_and_snapshotted(self):
        srb.cmd_backup(self.cfg, Args(no_forget=True))
        con = sqlite3.connect(self.src / "app.db")
        con.execute("insert into t values (8)")
        con.commit()
        con.close()
        srb.cmd_backup(self.cfg, Args(no_forget=True))
        self.assertGreaterEqual(len(self.snaps()), 2)
        out = self.base / "out"
        srb.cmd_restore(self.cfg, Args(name="demo", to=str(out)))
        rows = sqlite3.connect(next(out.rglob("app.db"))).execute("select count(*) from t").fetchone()
        self.assertEqual(rows, (2,))


class Config(unittest.TestCase):
    def test_settings_require_a_restic_table(self):
        with self.assertRaises(srb.ResticError):
            srb.settings({"dataset": []})
        s = srb.settings({"restic": {"repository": "/r", "password_file": "/p"}})
        self.assertEqual((s["keep_daily"], s["keep_weekly"], s["keep_monthly"], s["keep_yearly"]), (7, 5, 12, 3))
        self.assertEqual(s["compression"], "max")

    def test_flash_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(srb.sdb, "disk_kind", return_value="flash"):
            with self.assertRaises(srb.sdb.BackupError):
                srb.check_repository_location(str(Path(t, "r")))


if __name__ == "__main__":
    unittest.main()
