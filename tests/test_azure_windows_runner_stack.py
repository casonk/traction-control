"""Static checks for the Azure Windows runner stack (no Azure or Terraform calls)."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "azure_windows_runner_stack.sh"
TF = ROOT / "infra" / "azure-windows-runner" / "terraform"


def tf_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(TF.glob("*.tf")))


class WrapperTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False)

    def test_help_lists_only_the_three_actions(self) -> None:
        proc = self.run_script("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for action in ("plan", "apply", "destroy"):
            self.assertIn(action, proc.stdout)
        self.assertNotIn("adopt", proc.stdout)

    def test_unknown_action_is_refused(self) -> None:
        self.assertNotEqual(self.run_script("adopt").returncode, 0)
        self.assertNotEqual(self.run_script().returncode, 0)

    def test_confirmations_are_typed(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("confirm APPLY", text)
        self.assertIn("confirm DESTROY", text)

    def test_state_is_outside_the_repository_and_separate_from_linux(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("traction-control/azure-windows-runner", text)
        self.assertNotIn("azure-runner/terraform", text.replace("azure-windows-runner/terraform", ""))


class TerraformTests(unittest.TestCase):
    def test_no_inbound_is_allowed(self) -> None:
        text = tf_text()
        self.assertIn('name                       = "DenyAllInbound"', text)
        self.assertNotRegex(text, r'access\s*=\s*"Allow"')
        self.assertNotIn("3389", text)
        self.assertNotIn("5985", text)

    def test_password_is_generated_not_declared(self) -> None:
        text = tf_text()
        self.assertIn('resource "random_password" "admin"', text)
        self.assertNotRegex(text, r'variable\s+"admin_password"')

    def test_group_is_separate_from_the_linux_pool(self) -> None:
        text = tf_text()
        self.assertIn('default     = "rg-win-runner"', text)
        self.assertNotIn("rg-arc-pilot", text)
        self.assertNotIn("10.240.", text)  # the Linux stack's address space

    def test_compute_is_windows_and_auto_shuts_down(self) -> None:
        text = tf_text()
        self.assertIn("azurerm_windows_virtual_machine", text)
        self.assertIn("azurerm_dev_test_global_vm_shutdown_schedule", text)

    def test_no_credentials_or_subscription_ids_are_committed(self) -> None:
        text = tf_text() + SCRIPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text))
        self.assertNotRegex(text, r"ghp_|github_pat_|BEGIN [A-Z ]*PRIVATE KEY")


if __name__ == "__main__":
    unittest.main()
