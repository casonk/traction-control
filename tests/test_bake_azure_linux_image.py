"""Static checks for the Linux golden-image bake (no Azure calls)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bake_azure_linux_image.sh"
INSIDE = ROOT / "config" / "github-runners" / "linux-vm" / "bake-image.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
TF = (ROOT / "infra" / "azure-windows-runner" / "terraform" / "main.tf").read_text(encoding="utf-8")


class WrapperTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        # The script insists on a real (non-symlink) public key; do not depend on the machine having one.
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "id_test.pub"
            key.write_text("ssh-ed25519 AAAA-test-key\n", encoding="utf-8")
            env = {**os.environ, "AZURE_RUNNER_SSH_PUBLIC_KEY_FILE": str(key)}
            return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False, env=env)

    def test_dry_run_changes_nothing_and_names_the_plan(self) -> None:
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing was changed", proc.stdout)
        self.assertIn("linux-runner", proc.stdout)

    def test_unknown_argument_is_refused(self) -> None:
        self.assertNotEqual(self.run_script("--nope").returncode, 0)

    def test_the_bake_is_confirmed_by_typing(self) -> None:
        self.assertIn('[[ "$answer" == "BAKE" ]]', TEXT)

    def test_it_cleans_up_every_resource_a_failed_run_could_leave(self) -> None:
        for needle in ("az vm delete", "az network nic delete", "az network public-ip delete", "az disk delete",
                       "az network nsg delete", "az sig image-version delete", "trap cleanup EXIT"):
            self.assertIn(needle, TEXT)

    def test_it_waits_for_the_vm_agent_and_does_not_open_a_per_vm_nsg(self) -> None:
        self.assertIn("wait_for_agent", TEXT)
        self.assertIn('--nsg ""', TEXT)

    def test_it_asks_for_trusted_launch_not_the_gated_standard_type(self) -> None:
        self.assertIn("--security-type TrustedLaunch", TEXT)
        self.assertNotIn("--security-type Standard", TEXT)

    def test_it_verifies_the_image_with_the_brokers_start_script(self) -> None:
        for needle in ("runuser -u runner -- ./run.sh --jitconfig", "runner-logs", "--verify-only"):
            self.assertIn(needle, TEXT)


class InsideTheVMTests(unittest.TestCase):
    def test_the_runner_download_is_digest_verified_and_refused_otherwise(self) -> None:
        text = INSIDE.read_text(encoding="utf-8")
        self.assertIn("digest mismatch", text)
        self.assertIn("refusing an unverified file", text)

    def test_the_image_leaves_a_runner_user_and_the_runner_directory(self) -> None:
        text = INSIDE.read_text(encoding="utf-8")
        for needle in ("useradd", "/opt/actions-runner", "NOPASSWD", "usermod -aG docker runner"):
            self.assertIn(needle, text)

    def test_it_parses_as_shell(self) -> None:
        self.assertEqual(subprocess.run(["bash", "-n", str(INSIDE)]).returncode, 0)


class TerraformTests(unittest.TestCase):
    def test_the_linux_definition_is_trusted_launch(self) -> None:
        self.assertIn("trusted_launch_enabled = true", TF)
        self.assertNotIn("trusted_launch_supported", TF)


if __name__ == "__main__":
    unittest.main()
