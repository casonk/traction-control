"""Focused regressions for the render-only Air-primary coordinator."""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_air_primary as air


class AirPrimaryCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "air-primary.local.toml"
        self.account = __import__("pwd").getpwuid(os.getuid()).pw_name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _owner_write(path: Path, payload: str | bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            path.write_text(payload, encoding="utf-8")
        path.chmod(0o600)

    def _config_text(
        self,
        *,
        util_root: Path | None = None,
        mode: str = "render-only",
        air_address: str = "10.44.0.254/32",
        mini_address: str = "10.44.0.241/32",
        pro_address: str = "10.44.0.242/32",
        native_smb_enabled: bool = True,
        snowbridge_web_enabled: bool = False,
    ) -> str:
        util_root = util_root or self.root / "util-repos"
        runtime = Path(sys.executable).resolve()
        return f"""schema_version = 1
mode = {json.dumps(mode)}
generation = 7
deployment_id = "air-primary-test"

[network]
wireguard_interface = "utun7"
air_address = {json.dumps(air_address)}
mini_address = {json.dumps(mini_address)}
pro_address = {json.dumps(pro_address)}

[repositories]
clockwork = {json.dumps(os.fspath(util_root / "clockwork"))}
snowbridge = {json.dumps(os.fspath(util_root / "snowbridge"))}
wiring_harness = {json.dumps(os.fspath(util_root / "wiring-harness"))}

[runtime]
python = {json.dumps(os.fspath(runtime))}
caddy_binary = {json.dumps(os.fspath(self.root / "bin/caddy"))}

[clockwork]
python = {json.dumps(os.fspath(runtime))}
environment_file = {json.dumps(os.fspath(self.root / "clockwork.env"))}
backend_host = "127.0.0.1"
backend_port = 5001
edge_port = 8443

[snowbridge]
share_name = "snowbridge"
share_path = {json.dumps(os.fspath(self.root / "share"))}
expected_account = {json.dumps(self.account)}
native_smb_enabled = {str(native_smb_enabled).lower()}
web_backend_8080_enabled = {str(snowbridge_web_enabled).lower()}
web_backend_port = 8080
web_edge_port = 8444

[wiring_harness]
certs_dir = {json.dumps(os.fspath(self.root / "certs"))}
"""

    def _load(self, **changes: object) -> air.AirConfig:
        self._owner_write(self.config_path, self._config_text(**changes))
        return air.load_config(self.config_path, repo_root=REPOSITORY_ROOT)

    def test_init_is_owner_only_and_never_overwrites(self) -> None:
        target = self.root / "nested" / "air-primary.local.toml"
        created = air.initialize(target)
        self.assertEqual(created, target)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        original = target.read_bytes()
        with self.assertRaises(air.CoordinatorError) as context:
            air.initialize(target)
        self.assertEqual(context.exception.category, "unsafe_configuration")
        self.assertEqual(target.read_bytes(), original)

    def test_init_refuses_a_publishable_git_destination(self) -> None:
        worktree = self.root / "worktree"
        worktree.mkdir()
        subprocess.run(
            ["git", "init", "-q", worktree],
            check=True,
            capture_output=True,
            text=True,
        )
        target = worktree / "config" / "air-primary.local.toml"
        with self.assertRaises(air.CoordinatorError) as context:
            air.initialize(target)
        self.assertEqual(context.exception.category, "unsafe_configuration")
        self.assertFalse(target.exists())

    def test_rejects_non_render_mode_placeholder_and_duplicate_addresses(self) -> None:
        for changes in (
            {"mode": "activate"},
            {"air_address": "<air-address>/32"},
            {"air_address": "203.0.113.9/32"},
            {"pro_address": "10.44.0.241/32"},
        ):
            with self.subTest(changes=changes):
                self._owner_write(self.config_path, self._config_text(**changes))
                with self.assertRaises(air.CoordinatorError) as context:
                    air.load_config(self.config_path, repo_root=REPOSITORY_ROOT)
                self.assertEqual(context.exception.category, "unsafe_configuration")

        self._owner_write(
            self.config_path,
            self._config_text().replace("backend_port = 5001", "backend_port = 5000"),
        )
        with self.assertRaises(air.CoordinatorError) as context:
            air.load_config(self.config_path, repo_root=REPOSITORY_ROOT)
        self.assertEqual(context.exception.category, "unsafe_configuration")

        for native_smb_line in ("", 'native_smb_enabled = "true"'):
            with self.subTest(native_smb_line=native_smb_line):
                self._owner_write(
                    self.config_path,
                    self._config_text().replace(
                        "native_smb_enabled = true", native_smb_line
                    ),
                )
                with self.assertRaises(air.CoordinatorError) as context:
                    air.load_config(self.config_path, repo_root=REPOSITORY_ROOT)
                self.assertEqual(context.exception.category, "unsafe_configuration")

    def test_cli_contract_probe_fails_closed_on_missing_help_token(self) -> None:
        config = self._load()
        coordinator = air.AirPrimaryCoordinator(
            config, repo_root=self.root / "traction-control"
        )
        incomplete = subprocess.CompletedProcess(
            ["probe"], 0, "--manifest --target\n", ""
        )
        with (
            mock.patch.object(air, "_run", return_value=incomplete),
            self.assertRaises(air.CoordinatorError) as context,
        ):
            coordinator._probe_api()
        self.assertEqual(context.exception.category, "api_mismatch")

    def test_native_smb_disabled_skips_only_its_api_probes(self) -> None:
        config = self._load(native_smb_enabled=False, snowbridge_web_enabled=True)
        coordinator = air.AirPrimaryCoordinator(
            config, repo_root=self.root / "traction-control"
        )
        complete_help = subprocess.CompletedProcess(
            ["probe"],
            0,
            "--manifest --target --unit-dir launchd-user "
            "--services --certs-dir --output-dir --caddy-binary --validate-caddy\n",
            "",
        )
        with mock.patch.object(air, "_run", return_value=complete_help) as run_mock:
            coordinator._probe_api()

        commands = [
            [os.fspath(part) for part in call.args[0]]
            for call in run_mock.call_args_list
        ]
        self.assertEqual(len(commands), 2)
        self.assertFalse(
            any(
                part.endswith("macos_smb_plan.py")
                for command in commands
                for part in command
            )
        )
        self.assertTrue(
            any(
                part.endswith("render_macos_private_edge.py")
                for command in commands
                for part in command
            )
        )

    def test_staged_registry_omits_snowbridge_until_8080_is_enabled(self) -> None:
        config = self._load(snowbridge_web_enabled=False)
        coordinator = air.AirPrimaryCoordinator(
            config, repo_root=self.root / "traction-control"
        )
        staging = self.root / "stage-disabled"
        staging.mkdir(mode=0o700)
        staged = coordinator._stage_inputs(staging)
        local = staged["wiring_local"].read_text()
        self.assertIn('wireguard_interface = "utun7"', local)
        self.assertIn('macos_edge_role = "clockwork"', local)
        self.assertNotIn('macos_edge_role = "snowbridge"', local)
        self.assertIn('CLOCKWORK_WEB_PORT = "5001"', staged["clockwork"].read_text())
        self.assertIn("snowbridge", staged)
        for path in staged.values():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        enabled = replace(config, snowbridge_web_enabled=True)
        coordinator = air.AirPrimaryCoordinator(
            enabled, repo_root=self.root / "traction-control"
        )
        staging = self.root / "stage-enabled"
        staging.mkdir(mode=0o700)
        local = coordinator._stage_inputs(staging)["wiring_local"].read_text()
        self.assertIn('macos_edge_role = "snowbridge"', local)
        self.assertIn("port = 8080", local)

    def test_native_smb_disabled_keeps_web_role_and_omits_smb_inputs(self) -> None:
        config = replace(
            self._load(native_smb_enabled=False, snowbridge_web_enabled=True),
            inventory_file=self.root / "missing-inventory.json",
        )
        coordinator = air.AirPrimaryCoordinator(
            config, repo_root=self.root / "traction-control"
        )
        staging = self.root / "stage-native-smb-disabled"
        staging.mkdir(mode=0o700)
        staged = coordinator._stage_inputs(staging)

        self.assertNotIn("snowbridge", staged)
        self.assertNotIn("inventory", staged)
        self.assertFalse((staging / "inputs/snowbridge-air-smb.toml").exists())
        self.assertFalse((staging / "inputs/snowbridge-inventory.json").exists())
        local = staged["wiring_local"].read_text()
        self.assertIn('macos_edge_role = "snowbridge"', local)
        self.assertIn("port = 8080", local)

    def test_native_smb_disabled_skips_share_and_inventory_prerequisites(self) -> None:
        environment = self.root / "clockwork.env"
        self._owner_write(environment, f"CLOCKWORK_WEB_SECRET={'x' * 32}\n")
        caddy = self.root / "bin/caddy"
        self._owner_write(caddy, "#!/bin/sh\nexit 0\n")
        caddy.chmod(0o700)
        certs = self.root / "certs"
        certs.mkdir(mode=0o700)
        for name in ("server.crt", "server.key", "ca.crt"):
            self._owner_write(certs / name, "synthetic\n")

        config = replace(
            self._load(native_smb_enabled=False, snowbridge_web_enabled=True),
            expected_account="different-safe-account",
            inventory_file=self.root / "missing-inventory.json",
        )
        coordinator = air.AirPrimaryCoordinator(config, repo_root=REPOSITORY_ROOT)

        def fake_run(
            command: list[str | os.PathLike[str]], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            argv = [os.fspath(part) for part in command]
            output = "Python 3.11.0\n" if "--version" in argv else ""
            return subprocess.CompletedProcess(argv, 0, output, "")

        with (
            mock.patch.object(coordinator, "_validate_repositories", return_value={}),
            mock.patch.object(coordinator, "_probe_api"),
            mock.patch.object(air, "_run", side_effect=fake_run),
        ):
            report = coordinator.validate()

        self.assertEqual(report["snowbridge_inventory"], "disabled")
        self.assertFalse(report["snowbridge_native_smb_enabled"])
        self.assertEqual(
            report["snowbridge_native_smb_disabled_reason"],
            air.NATIVE_SMB_DISABLED_REASON,
        )
        self.assertTrue(report["snowbridge_web_backend_8080_enabled"])

    def test_preflight_classifies_missing_and_unsafe_prerequisites_differently(
        self,
    ) -> None:
        util_root = REPOSITORY_ROOT.parent
        config = self._load(util_root=util_root)
        coordinator = air.AirPrimaryCoordinator(config, repo_root=REPOSITORY_ROOT)
        with self.assertRaises(air.CoordinatorError) as missing:
            coordinator.validate()
        self.assertEqual(missing.exception.category, "missing_prerequisite")

        environment = self.root / "clockwork.env"
        self._owner_write(environment, "CLOCKWORK_WEB_SECRET=too-short\n")
        environment.chmod(0o644)
        with self.assertRaises(air.CoordinatorError) as unsafe:
            coordinator.validate()
        self.assertEqual(unsafe.exception.category, "unsafe_configuration")
        self.assertNotEqual(missing.exception.exit_code, unsafe.exception.exit_code)

    def test_render_invokes_only_original_sibling_clis_and_is_immutable(self) -> None:
        util_root = self.root / "util-repos"
        traction = util_root / "traction-control"
        traction.mkdir(parents=True)
        for name in ("clockwork", "snowbridge", "wiring-harness"):
            (util_root / name).mkdir()
        config = self._load(util_root=util_root)
        coordinator = air.AirPrimaryCoordinator(config, repo_root=traction)
        calls: list[list[str]] = []

        def owner_file(path: Path, payload: str | bytes) -> None:
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._owner_write(path, payload)

        def fake_run(
            command: list[str | os.PathLike[str]],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            argv = [os.fspath(part) for part in command]
            calls.append(argv)
            if "clockwork" in argv and "install" in argv:
                output = Path(argv[argv.index("--unit-dir") + 1])
                payload = {
                    "Label": "io.github.casonk.clockwork.clockwork-web-macos",
                    "WorkingDirectory": os.fspath(config.clockwork_repo),
                    "EnvironmentVariables": {
                        "CLOCKWORK_WEB_HOST": "127.0.0.1",
                        "CLOCKWORK_WEB_PORT": "5001",
                    },
                }
                owner_file(
                    output / "clockwork-web-macos.plist", plistlib.dumps(payload)
                )
            elif any(part.endswith("macos_smb_plan.py") for part in argv):
                output = Path(argv[argv.index("--output") + 1])
                owner_file(
                    output / "activation-plan.json",
                    json.dumps(
                        {
                            "activation_supported": False,
                            "wireguard_boundary": {
                                "interface": "utun7",
                                "host_address": "10.44.0.254/32",
                                "allowed_client_addresses": [
                                    "10.44.0.241/32",
                                    "10.44.0.242/32",
                                ],
                            },
                        }
                    ),
                )
                owner_file(output / "snowbridge-smb.pf", "# inert\n")
            elif any(part.endswith("render_macos_private_edge.py") for part in argv):
                output = Path(argv[argv.index("--output-dir") + 1])
                owner_file(output / "Caddyfile", "# inert\n")
                owner_file(
                    output / "manifest.json",
                    json.dumps(
                        {
                            "activation": "render-only",
                            "wireguard_interface": "utun7",
                            "wireguard_bind": "10.44.0.254/32",
                            "caddy": {"validated": True},
                            "services": [{"role": "clockwork"}],
                        }
                    ),
                )
                owner_file(
                    output / "dev.user.wiring-harness.macos-private-edge.plist",
                    plistlib.dumps(
                        {"Label": "dev.user.wiring-harness.macos-private-edge"}
                    ),
                )
            return subprocess.CompletedProcess(argv, 0, "rendered\n", "")

        preflight = {"ok": True, "category": "ready", "repositories": {}}
        with (
            mock.patch.object(coordinator, "_preflight", return_value=preflight),
            mock.patch.object(coordinator, "_assert_snowbridge_output_ignored"),
            mock.patch.object(air, "_run", side_effect=fake_run),
        ):
            manifest = coordinator.render()
            self.assertFalse(manifest["activation_supported"])
            with self.assertRaises(air.CoordinatorError) as context:
                coordinator.render()
            self.assertEqual(context.exception.category, "unsafe_configuration")

        flattened = [token for command in calls for token in command]
        for forbidden in ("sudo", "launchctl", "pfctl", "sharing"):
            self.assertNotIn(forbidden, flattened)
        self.assertTrue(any(part.endswith("macos_smb_plan.py") for part in flattened))
        self.assertTrue(
            any(part.endswith("render_macos_private_edge.py") for part in flattened)
        )
        self.assertFalse(
            (
                traction
                / "artifacts"
                / "air-primary"
                / "generation-7"
                / "failure-report.json"
            ).exists()
        )

    def test_render_omits_native_smb_but_keeps_independent_web_edge(self) -> None:
        util_root = self.root / "disabled-util-repos"
        traction = util_root / "traction-control"
        traction.mkdir(parents=True)
        for name in ("clockwork", "snowbridge", "wiring-harness"):
            (util_root / name).mkdir()
        config = replace(
            self._load(
                util_root=util_root,
                native_smb_enabled=False,
                snowbridge_web_enabled=True,
            ),
            inventory_file=self.root / "missing-inventory.json",
        )
        coordinator = air.AirPrimaryCoordinator(config, repo_root=traction)
        coordinator.snowbridge_output.mkdir(parents=True, mode=0o700)
        sentinel = coordinator.snowbridge_output / "existing-evidence.txt"
        self._owner_write(sentinel, "preserve\n")
        calls: list[list[str]] = []

        def owner_file(path: Path, payload: str | bytes) -> None:
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._owner_write(path, payload)

        def fake_run(
            command: list[str | os.PathLike[str]], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            argv = [os.fspath(part) for part in command]
            calls.append(argv)
            if "clockwork" in argv and "install" in argv:
                output = Path(argv[argv.index("--unit-dir") + 1])
                owner_file(
                    output / "clockwork-web-macos.plist",
                    plistlib.dumps(
                        {
                            "WorkingDirectory": os.fspath(config.clockwork_repo),
                            "EnvironmentVariables": {
                                "CLOCKWORK_WEB_HOST": "127.0.0.1",
                                "CLOCKWORK_WEB_PORT": "5001",
                            },
                        }
                    ),
                )
            elif any(part.endswith("render_macos_private_edge.py") for part in argv):
                output = Path(argv[argv.index("--output-dir") + 1])
                owner_file(output / "Caddyfile", "# inert web edge\n")
                owner_file(
                    output / "manifest.json",
                    json.dumps(
                        {
                            "activation": "render-only",
                            "wireguard_interface": "utun7",
                            "wireguard_bind": "10.44.0.254/32",
                            "caddy": {"validated": True},
                            "services": [
                                {"role": "clockwork"},
                                {"role": "snowbridge"},
                            ],
                        }
                    ),
                )
                owner_file(
                    output / "dev.user.wiring-harness.macos-private-edge.plist",
                    plistlib.dumps(
                        {"Label": "dev.user.wiring-harness.macos-private-edge"}
                    ),
                )
            return subprocess.CompletedProcess(argv, 0, "rendered\n", "")

        preflight = {"ok": True, "category": "ready", "repositories": {}}
        with (
            mock.patch.object(coordinator, "_preflight", return_value=preflight),
            mock.patch.object(
                coordinator, "_assert_snowbridge_output_ignored"
            ) as ignored_mock,
            mock.patch.object(air, "_run", side_effect=fake_run),
        ):
            manifest = coordinator.render()

        ignored_mock.assert_not_called()
        flattened = [token for command in calls for token in command]
        self.assertFalse(any(part.endswith("macos_smb_plan.py") for part in flattened))
        self.assertTrue(
            any(part.endswith("render_macos_private_edge.py") for part in flattened)
        )
        self.assertFalse(manifest["snowbridge_native_smb_enabled"])
        self.assertEqual(
            manifest["snowbridge_native_smb_disabled_reason"],
            air.NATIVE_SMB_DISABLED_REASON,
        )
        self.assertTrue(manifest["snowbridge_web_backend_8080_enabled"])
        self.assertEqual(len(manifest["artifacts"]), 4)
        self.assertFalse(
            any("snowbridge" in artifact for artifact in manifest["artifacts"])
        )
        generation = traction / "artifacts/air-primary/generation-7"
        self.assertFalse((generation / "inputs/snowbridge-air-smb.toml").exists())
        self.assertFalse((generation / "logs/snowbridge.stdout.log").exists())
        self.assertEqual(sentinel.read_text(), "preserve\n")

    def test_child_timeout_consumes_generation_with_failure_report(self) -> None:
        util_root = self.root / "util-repos"
        traction = util_root / "traction-control"
        traction.mkdir(parents=True)
        for name in ("clockwork", "snowbridge", "wiring-harness"):
            (util_root / name).mkdir()
        config = replace(self._load(util_root=util_root), generation=8)
        coordinator = air.AirPrimaryCoordinator(config, repo_root=traction)
        preflight = {"ok": True, "category": "ready", "repositories": {}}
        timeout = subprocess.TimeoutExpired(["clockwork"], 60)
        with (
            mock.patch.object(coordinator, "_preflight", return_value=preflight),
            mock.patch.object(coordinator, "_assert_snowbridge_output_ignored"),
            mock.patch.object(air, "_run", side_effect=timeout),
            self.assertRaises(air.CoordinatorError) as context,
        ):
            coordinator.render()
        self.assertEqual(context.exception.category, "render_failure")
        report_path = (
            traction
            / "artifacts"
            / "air-primary"
            / "generation-8"
            / "failure-report.json"
        )
        report = json.loads(report_path.read_text())
        self.assertEqual(report["category"], "render_failure")
        self.assertFalse((report_path.parent / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
