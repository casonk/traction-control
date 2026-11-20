"""The portfolio audit counts a bulk path on flash as a gap, and stays clean for one on a spinning disk."""

import json
import os
import time
import subprocess
import tempfile
import unittest
from pathlib import Path

AUDIT = Path(__file__).resolve().parent.parent / "scripts" / "portfolio-audit.sh"


def run_audit(rota: str, env_text: str, *, state: dict | None = None, datasets_config: str | None = None, configured: bool = True, fake_restic: bool = False) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as t:
        root, bin_, log = Path(t, "root"), Path(t, "bin"), Path(t, "log")
        (root / "proj").mkdir(parents=True)
        bin_.mkdir()
        (root / "proj" / "x.env.local").write_text(env_text)
        (bin_ / "findmnt").write_text("#!/bin/sh\necho /dev/fakedisk\n")
        (bin_ / "lsblk").write_text(f'#!/bin/sh\necho "disk {rota}"\n')
        if fake_restic:
            # The missing-repository check returns before restic runs; it only needs the binary to exist.
            (bin_ / "restic").write_text("#!/bin/sh\nexit 0\n")
        for f in bin_.iterdir():
            f.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_}:{os.environ['PATH']}",
            "PORTFOLIO_ROOT": str(root),
            "LOG_DIR": str(log),
            "HOME": str(Path(t, "home")),
            "TRACTION_CONTROL_STATE_DIR": str(Path(t, "state")),
            "STATE_DATASETS_CONFIG": datasets_config or str(Path(t, "absent.toml")),
            "LOCAL_STATE_CONFIG": str(Path(t, "local-state.toml")) if configured else str(Path(t, "absent-local.toml")),
        }
        if configured:
            Path(t, "local-state.toml").write_text("")
        if state is not None:
            (Path(t, "state") / "local-state").mkdir(parents=True)
            (Path(t, "state") / "local-state" / "state.json").write_text(json.dumps(state))
        p = subprocess.run(["bash", str(AUDIT)], env=env, capture_output=True, text=True, timeout=120, check=False)
        return p.returncode, p.stdout + p.stderr


class AuditBulkStorage(unittest.TestCase):
    def test_flash_path_is_counted_and_fails_the_audit(self):
        rc, out = run_audit("0", "X_DOWNLOAD_DIR=/fastdisk/dl\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("bulk storage: bulk path on flash storage: X_DOWNLOAD_DIR=/fastdisk/dl", out)
        self.assertIn("bulk on flash  : 1", out)

    def test_spinning_disk_is_clean(self):
        _rc, out = run_audit("1", "X_DOWNLOAD_DIR=/slowdisk/dl\n")
        self.assertIn("every configured bulk path is on a spinning disk", out)
        self.assertIn("bulk on flash  : 0", out)
        self.assertNotIn("bulk storage: bulk path", out)

    def test_no_bulk_settings_is_clean(self):
        _rc, out = run_audit("0", "OTHER=/fastdisk/x\n")
        self.assertIn("bulk on flash  : 0", out)


class AuditStateBackups(unittest.TestCase):
    def test_a_machine_that_was_never_backed_up_is_a_gap(self):
        rc, out = run_audit("1", "", state=None)
        self.assertEqual(rc, 1, out)
        self.assertIn("local state: local state has never been backed up", out)
        self.assertIn("state backups  : 1", out)

    def test_an_unconfigured_machine_skips_without_counting(self):
        _rc, out = run_audit("1", "", state=None, configured=False)
        self.assertIn("local configuration bundle SKIPPED", out)
        self.assertIn("state backups  : 0", out)

    def test_a_stale_bundle_is_a_gap_and_a_fresh_one_is_clean(self):
        _rc, out = run_audit("1", "", state={"last_success": int(time.time()) - 5 * 86400})
        self.assertIn("state backups  : 1", out)
        _rc, out = run_audit("1", "", state={"last_success": int(time.time()) - 3600})
        self.assertIn("local configuration bundle is current", out)
        self.assertIn("state backups  : 0", out)
        self.assertIn("dataset backups SKIPPED", out)

    def test_dataset_backups_that_are_missing_are_counted(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t, "datasets.toml")
            cfg.write_text(f'dest = "{t}/dest"\n[[dataset]]\nname = "x"\npaths = ["/nope"]\n')
            _rc, out = run_audit("1", "", state={"last_success": int(time.time())}, datasets_config=str(cfg))
        self.assertIn("dataset backup: x: no backup", out)
        self.assertIn("state backups  : 1", out)

    def test_a_missing_restic_repository_is_counted(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t, "datasets.toml")
            cfg.write_text(f'dest = "{t}/dest"\n[restic]\nrepository = "{t}/repo"\npassword_file = "{t}/pw"\n'
                           '[[dataset]]\nname = "x"\npaths = ["/nope"]\n')
            _rc, out = run_audit("1", "", state={"last_success": int(time.time())}, datasets_config=str(cfg), fake_restic=True)
        self.assertIn("restic backup: no restic repository", out)


if __name__ == "__main__":
    unittest.main()
