import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_bulk_storage as cbs  # noqa: E402


class Result:
    def __init__(self, stdout=""):
        self.stdout = stdout


def fake_run(mapping):
    """findmnt -> /dev/<name>; lsblk -> ROTA from mapping[path-prefix]."""

    def run(cmd, **_kw):
        if cmd[0] == "findmnt":
            p = cmd[-1]
            for prefix, (dev, _rota) in mapping.items():
                if p.startswith(prefix):
                    return Result(f"/dev/{dev}[/sub]\n")
            return Result("tmpfs\n")
        for _prefix, (dev, rota) in mapping.items():
            if cmd[-1] == f"/dev/{dev}":
                return Result(f"part 0\ndisk {rota}\n")
        return Result("")

    return run


class DiskKind(unittest.TestCase):
    def test_hdd_flash_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            hdd, ssd, other = Path(t, "hdd"), Path(t, "ssd"), Path(t, "mem")
            for d in (hdd, ssd, other):
                d.mkdir()
            run = fake_run({str(hdd): ("sdb", 1), str(ssd): ("nvme0n1", 0)})
            self.assertEqual(cbs.disk_kind(str(hdd / "new" / "x"), run=run), "hdd")
            self.assertEqual(cbs.disk_kind(str(ssd), run=run), "flash")
            self.assertEqual(cbs.disk_kind(str(other), run=run), "unknown")


class Check(unittest.TestCase):
    def test_env_and_config_findings(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "repo" / "config").mkdir(parents=True)
            (root / "repo" / "config" / "x.env.local").write_text(
                "X_DOWNLOAD_DIR=/fast/dl\nY_BACKUP_DIR='/slow/bk'\nZ_DOWNLOAD_DIR=relative\n# W_DOWNLOAD_DIR=/fast/c\nOTHER=/fast/o\n"
            )
            (root / "repo" / "config" / "x.env.example").write_text("A_DOWNLOAD_DIR=/fast/ex\n")
            cfg = root / "bulk.toml"
            cfg.write_text('bulk_paths = ["/fast/declared", "/fast/ok"]\nallow_flash = ["/fast/ok"]\n')
            kind = lambda p: "hdd" if p.startswith("/slow") else "flash"  # noqa: E731
            got = {(f["name"], f["path"]) for f in cbs.check(root, cfg, kind=kind)}
            self.assertEqual(
                got, {("bulk_paths", "/fast/declared"), ("X_DOWNLOAD_DIR", "/fast/dl")}
            )

    def test_unknown_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t, "bulk.toml")
            cfg.write_text('bulk_paths = ["/x"]\n')
            self.assertEqual(len(cbs.check(Path(t), cfg, kind=lambda p: "unknown")), 1)

    def test_missing_config_is_empty(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(cbs.check(Path(t), Path(t, "none.toml"), kind=lambda p: "flash"), [])


if __name__ == "__main__":
    unittest.main()
