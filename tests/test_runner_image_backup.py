"""Static checks for the golden-image backup script and the Git pin (no Azure calls)."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup_azure_runner_images.sh"
TEXT = BACKUP.read_text(encoding="utf-8")
WIN_PS1 = (ROOT / "config" / "github-runners" / "windows" / "bake-image.ps1").read_text(encoding="utf-8")
WIN_BAKE = (ROOT / "scripts" / "bake_azure_windows_image.sh").read_text(encoding="utf-8")
LINUX_IN_VM = (ROOT / "config" / "github-runners" / "linux-vm" / "bake-image.sh").read_text(encoding="utf-8")


class BackupScriptTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(BACKUP), *args], capture_output=True, text=True, check=False)

    def test_a_dry_run_changes_nothing_and_exits_cleanly(self) -> None:
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing was changed", proc.stdout)
        self.assertIn("windows-runner", proc.stdout)
        self.assertIn("linux-runner", proc.stdout)

    def test_only_filters_and_rejects_unknown_values(self) -> None:
        only = self.run_script("--dry-run", "--only", "linux")
        self.assertEqual(only.returncode, 0, only.stderr)
        self.assertNotIn("windows-runner", only.stdout)
        self.assertNotEqual(self.run_script("--only", "mac").returncode, 0)
        self.assertNotEqual(self.run_script("--nope").returncode, 0)

    def test_it_is_confirmed_by_typing(self) -> None:
        self.assertIn('[[ "$answer" == "BACKUP" ]]', TEXT)

    def test_the_download_link_never_reaches_a_command_line_or_the_screen(self) -> None:
        self.assertIn("-K \"$cfg\"", TEXT)  # curl reads the URL from an owner-only config file
        self.assertIn("umask 077", TEXT)
        self.assertNotRegex(TEXT, r'curl[^\n]*\$sas')
        # The only place the link is written is the owner-only curl config file.
        for line in TEXT.splitlines():
            if re.search(r'(echo|printf)[^\n]*\$sas', line):
                self.assertIn('> "$cfg"', line)
        self.assertIn("accessSAS", TEXT)  # the field name this Azure CLI returns

    def test_the_download_is_chunked_and_resumable_because_the_endpoint_drops_long_connections(self) -> None:
        self.assertIn("download_ranges", TEXT)
        self.assertIn('-r "$off-$end"', TEXT)
        self.assertIn("failed 15 times in a row", TEXT)
        self.assertNotIn("--continue-at", TEXT)  # curl's whole-file resume restarted from zero on this endpoint

    def test_it_proves_a_complete_download_and_a_restorable_copy(self) -> None:
        for needle in ("conectix", "diskSizeBytes", "sha256sum", "zstd -dc", "does not decompress to the downloaded disk"):
            self.assertIn(needle, TEXT)

    def test_it_always_revokes_the_link_and_deletes_the_temporary_disk(self) -> None:
        self.assertIn("trap cleanup EXIT", TEXT)
        self.assertIn("az disk revoke-access", TEXT)
        self.assertIn("az disk delete", TEXT)

    def test_the_cleanup_trap_cannot_turn_success_into_failure(self) -> None:
        # `[[ test ]] && cmd` as the last command of an EXIT trap makes `exit 0` return 1 under set -e.
        trap_body = TEXT[TEXT.index("cleanup() {"):TEXT.index("trap cleanup EXIT")]
        self.assertNotRegex(trap_body, r"^\s*\[\[.*\]\]\s*&&", re.M)

    def test_the_linux_disk_is_requested_as_trusted_launch_like_its_image(self) -> None:
        self.assertIn("--security-type TrustedLaunch", TEXT)

    def test_the_destination_is_owner_only(self) -> None:
        self.assertIn('install -d -m 700', TEXT)
        self.assertIn("chmod 600", TEXT)


class GitPinTests(unittest.TestCase):
    def test_the_windows_bake_installs_a_pinned_git_release_not_the_latest(self) -> None:
        self.assertIn("releases/tags/$GitTag", WIN_PS1)
        self.assertNotIn("releases/latest", WIN_PS1)
        self.assertIn("expected", WIN_PS1)

    def test_the_wrapper_pins_and_validates_the_tag(self) -> None:
        match = re.search(r'GIT_TAG="\$\{GIT_TAG:-(v[0-9.]+\.windows\.[0-9]+)\}"', WIN_BAKE)
        self.assertIsNotNone(match)
        self.assertIn("GIT_TAG must look like", WIN_BAKE)
        self.assertIn("$GitTag", WIN_BAKE)

    def test_the_linux_bake_records_what_it_installed(self) -> None:
        self.assertIn("/opt/runner-image-manifest.txt", LINUX_IN_VM)
        self.assertIn("dpkg-query", LINUX_IN_VM)


if __name__ == "__main__":
    unittest.main()
