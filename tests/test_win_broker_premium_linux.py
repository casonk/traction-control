"""The premium per-job Linux VM class must stay off unless someone asks for it."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "config" / "github-runners" / "windows" / "win-broker.yaml.template").read_text(encoding="utf-8")
INSTALLER = (ROOT / "scripts" / "install_azure_win_broker.sh").read_text(encoding="utf-8")
VARIABLES = (ROOT / "infra" / "azure-windows-runner" / "terraform" / "variables.tf").read_text(encoding="utf-8")
CONFIG_GO = (ROOT / "services" / "win-broker" / "internal" / "broker" / "config.go").read_text(encoding="utf-8")


class PremiumLinuxIsOptInTests(unittest.TestCase):
    def test_the_manifest_takes_the_class_from_the_installer(self) -> None:
        self.assertIn('{name: WIN_BROKER_LINUX_VM, value: "__LINUX_VM__"}', TEMPLATE)
        self.assertIn('{name: WIN_BROKER_AZURE_LINUX_IMAGE_ID, value: "__LINUX_IMAGE_ID__"}', TEMPLATE)

    def test_the_installer_defaults_to_off_and_needs_an_image(self) -> None:
        self.assertIn("WITH_LINUX=0", INSTALLER)
        self.assertRegex(INSTALLER, r"--with-linux-vm\) WITH_LINUX=1")
        self.assertIn("linux-image-version.txt", INSTALLER)
        self.assertIn("fail \"--with-linux-vm needs a Linux golden image", INSTALLER)

    def test_the_template_vm_is_off_by_default_so_a_plain_apply_cannot_bill_for_one(self) -> None:
        block = re.search(r'variable "enable_template_vm" \{(.*?)\n\}', VARIABLES, re.S)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1), r"default\s*=\s*false")

    def test_the_linux_image_definition_is_kept_but_does_not_enable_the_broker_class(self) -> None:
        block = re.search(r'variable "enable_linux_image_definition" \{(.*?)\n\}', VARIABLES, re.S)
        self.assertIsNotNone(block)
        # On, because an image version lives in it; the broker class is still gated separately.
        self.assertRegex(block.group(1), r"default\s*=\s*true")
        self.assertIn('{name: WIN_BROKER_LINUX_VM, value: "__LINUX_VM__"}', TEMPLATE)

    def test_only_the_exact_value_one_enables_it(self) -> None:
        self.assertIn('cfg.LinuxVM = getenv("WIN_BROKER_LINUX_VM") == "1"', CONFIG_GO)


if __name__ == "__main__":
    unittest.main()
