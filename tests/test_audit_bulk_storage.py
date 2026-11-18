"""The portfolio audit counts a bulk path on flash as a gap, and stays clean for one on a spinning disk."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

AUDIT = Path(__file__).resolve().parent.parent / "scripts" / "portfolio-audit.sh"


def run_audit(rota: str, env_text: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as t:
        root, bin_, log = Path(t, "root"), Path(t, "bin"), Path(t, "log")
        (root / "proj").mkdir(parents=True)
        bin_.mkdir()
        (root / "proj" / "x.env.local").write_text(env_text)
        (bin_ / "findmnt").write_text("#!/bin/sh\necho /dev/fakedisk\n")
        (bin_ / "lsblk").write_text(f'#!/bin/sh\necho "disk {rota}"\n')
        for f in bin_.iterdir():
            f.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_}:{os.environ['PATH']}",
            "PORTFOLIO_ROOT": str(root),
            "LOG_DIR": str(log),
            "HOME": str(Path(t, "home")),
        }
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


if __name__ == "__main__":
    unittest.main()
