"""Offline regressions for render-only Podman-on-WireGuard target bundles."""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import render_portfolio_sidecar_quadlets as quadlets  # noqa: E402


class PortfolioSidecarQuadletTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.config = self.root / "podman-mesh.local.json"
        self.targets_config = self.root / "targets.local.json"

    def _target(self, index: int) -> dict[str, object]:
        return {
            "target_id": f"TARGET_TEST_MESH_{index:03d}",
            "failure_domain": f"FAILURE_DOMAIN_TEST_MESH_{index:03d}",
            "image": (
                "registry.example.invalid/traction-control/sidecar-sftp@sha256:"
                + "2" * 64
            ),
            "unit_name": f"portfolio-sidecar-target-{index:03d}",
            "container_name": f"portfolio-sidecar-target-{index:03d}",
            "mesh_address": f"10.88.0.{10 + index}",
            "published_port": 2200 + index,
            "container_port": 2222,
            "repository_volume": f"portfolio-sidecar-repository-{index:03d}",
            "host_key_secret": f"portfolio-sidecar-host-key-{index:03d}",
            "authorized_keys_secret": (
                f"portfolio-sidecar-authorized-keys-{index:03d}"
            ),
        }

    def _payload(self, *, targets: int = 3) -> dict[str, object]:
        return {
            "schema_version": 1,
            "deployment_id": "DEPLOYMENT_TEST_PODMAN_MESH_001",
            "deployment_generation": 1,
            "coordinator_mode": "standalone-no-automatic-failover",
            "target_set_id": "TARGET_SET_TEST_MESH_001",
            "coordinator": {
                "image": (
                    "registry.example.invalid/traction-control/"
                    "sidecar-coordinator@sha256:" + "1" * 64
                ),
                "unit_name": "portfolio-sidecar-coordinator",
                "container_name": "portfolio-sidecar-coordinator",
            },
            "targets": [self._target(index) for index in range(1, targets + 1)],
        }

    def _targets_payload(
        self, deployment: dict[str, object] | None = None
    ) -> dict[str, object]:
        source = deployment or self._payload()
        targets = []
        for item in source["targets"]:  # type: ignore[union-attr]
            targets.append(
                {
                    "target_id": item["target_id"],
                    "repository_file": f"/synthetic/{item['target_id']}/repository",
                    "password_file": f"/synthetic/{item['target_id']}/password",
                    "identity_file": f"/synthetic/{item['target_id']}/identity",
                    "sftp_port": item["published_port"],
                    "mesh_address": item["mesh_address"],
                    "failure_domain": item["failure_domain"],
                }
            )
        return {
            "schema_version": 1,
            "registry_id": "test-portfolio",
            "registry_generation": 7,
            "target_generation": 4,
            "target_sets": [
                {
                    "target_set_id": source["target_set_id"],
                    "tier": "mesh-only",
                    "required_acks": len(targets) // 2 + 1,
                    "targets": targets,
                }
            ],
        }

    def _write(self, payload: object | None = None, *, path: Path | None = None) -> Path:
        destination = path or self.config
        destination.write_text(
            json.dumps(payload if payload is not None else self._payload(), indent=2)
            + "\n",
            encoding="utf-8",
        )
        destination.chmod(0o600)
        return destination

    def _load(self, payload: object | None = None) -> quadlets.Deployment:
        deployment = payload if payload is not None else self._payload()
        self._write(deployment)
        if isinstance(deployment, dict) and deployment.get("deployment_generation") != 0:
            self._write(self._targets_payload(deployment), path=self.targets_config)
            return quadlets.load_deployment(self.config, self.targets_config)
        return quadlets.load_deployment(self.config)

    def test_renders_owner_only_inactive_linux_native_bundle(self) -> None:
        deployment = self._load()
        output = self.root / "target-001.local.d"

        result = quadlets.render_target_bundle(
            deployment, "TARGET_TEST_MESH_001", output
        )

        self.assertEqual(result, output)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        expected = {
            "manifest.json",
            "portfolio-sidecar-target-001.container",
            "portfolio-sidecar-target-001-repository.volume",
        }
        self.assertEqual({path.name for path in output.iterdir()}, expected)
        for path in output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            self.assertNotIn("/synthetic/", path.read_text(encoding="utf-8"))

        self.assertFalse((output / "portfolio-sidecar-coordinator.container").exists())
        self.assertFalse((output / "portfolio-sidecar-target-002.container").exists())

        target = (output / "portfolio-sidecar-target-001.container").read_text(
            encoding="utf-8"
        )
        self.assertIn("@sha256:" + "2" * 64, target)
        self.assertIn("Pull=never", target)
        self.assertIn("Network=bridge", target)
        self.assertIn("PublishPort=10.88.0.11:2201:2222/tcp", target)
        self.assertIn("ReadOnly=true", target)
        self.assertIn("ReadOnlyTmpfs=false", target)
        self.assertIn("NoNewPrivileges=true", target)
        self.assertIn("DropCapability=ALL", target)
        self.assertIn("AddCapability=SYS_CHROOT", target)
        self.assertIn(
            "Secret=source=portfolio-sidecar-host-key-001,"
            "target=sidecar-host-key,type=mount,uid=0,gid=0,mode=0400",
            target,
        )
        self.assertIn(
            "Volume=portfolio-sidecar-target-001-repository.volume:"
            "/srv/portfolio-sidecar/repository:rw,nodev,nosuid,noexec,Z",
            target,
        )
        self.assertNotIn("[Install]", target)
        self.assertNotIn("WantedBy=", target)
        self.assertNotIn("AutoUpdate", target)
        self.assertNotIn("PodmanArgs", target)
        self.assertNotIn("/run/podman", target)
        self.assertNotIn("Network=host", target)
        self.assertNotIn("SecurityLabelDisable", target)
        self.assertNotIn("Privileged", target)
        self.assertNotIn("Wants=network-online.target", target)
        self.assertNotIn("After=network-online.target", target)
        self.assertNotIn("synthetic-secret-value", target)

        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["activation_ready"])
        self.assertFalse(manifest["activation_performed"])
        self.assertFalse(manifest["podman_invoked"])
        self.assertFalse(manifest["systemctl_invoked"])
        self.assertFalse(manifest["wireguard_configured"])
        self.assertFalse(manifest["secrets_created"])
        self.assertEqual(manifest["platform"], "linux-native")
        self.assertIn("macOS Podman machine", manifest["platform_boundary"])
        self.assertFalse(manifest["wireguard_attested"])
        self.assertEqual(manifest["registry_id"], "test-portfolio")
        self.assertEqual(manifest["registry_generation"], 7)
        self.assertEqual(manifest["target_generation"], 4)
        self.assertEqual(manifest["required_acks"], 2)
        self.assertRegex(manifest["target_document_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["target_topology_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(manifest["full_sidecar_target_validation_performed"])
        self.assertFalse(manifest["host_key_known_hosts_binding_validated"])
        self.assertFalse(manifest["repository_capacity_boundary_validated"])
        self.assertEqual(manifest["target"]["target_id"], "TARGET_TEST_MESH_001")
        self.assertEqual(len(manifest["artifacts"]), 2)

        coordinator_output = self.root / "coordinator.local.d"
        quadlets.render_coordinator_review_bundle(deployment, coordinator_output)
        coordinator = (
            coordinator_output / "portfolio-sidecar-coordinator.coordinator-review"
        ).read_text(encoding="utf-8")
        self.assertIn("REVIEW ONLY", coordinator)
        self.assertIn("QuadletActivationSupported=false", coordinator)
        self.assertIn("AutomaticFailoverSupported=false", coordinator)
        self.assertFalse(
            (coordinator_output / "portfolio-sidecar-coordinator.container").exists()
        )
        coordinator_manifest = json.loads(
            (coordinator_output / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(coordinator_manifest["target_count"], 3)
        self.assertNotIn("targets", coordinator_manifest)

    def test_tracked_synthetic_examples_share_one_topology(self) -> None:
        deployment_path = self.root / "example-deployment.local.json"
        targets_path = self.root / "example-targets.local.json"
        shutil.copyfile(
            REPO_ROOT / "config/portfolio-sidecar/podman-mesh.example.json",
            deployment_path,
        )
        shutil.copyfile(
            REPO_ROOT / "config/portfolio-sidecar/targets.example.json",
            targets_path,
        )
        deployment_path.chmod(0o600)
        targets_path.chmod(0o600)

        deployment = quadlets.load_deployment(deployment_path, targets_path)

        self.assertEqual(deployment.target_set_id, "TARGET_SET_EXAMPLE_MESH_001")
        self.assertEqual(len(deployment.targets), 3)
        self.assertTrue(all(target.published_port == 2222 for target in deployment.targets))

    def test_render_only_invokes_git_for_private_path_checks(self) -> None:
        original_run = subprocess.run
        commands: list[tuple[str, ...]] = []

        def guarded_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(tuple(command))
            if not command or command[0] != "git":
                self.fail(f"unexpected executable invocation: {command}")
            return original_run(command, **kwargs)

        with mock.patch.object(quadlets.subprocess, "run", side_effect=guarded_run):
            deployment = self._load()
            quadlets.render_target_bundle(
                deployment,
                "TARGET_TEST_MESH_001",
                self.root / "target-001.local.d",
            )

        self.assertGreaterEqual(len(commands), 2)
        self.assertTrue(all(command[0] == "git" for command in commands))

    def test_init_creates_inert_owner_only_config_and_render_refuses_it(self) -> None:
        deployment = quadlets.initialize_config(self.config)

        self.assertEqual(deployment.deployment_generation, 0)
        self.assertIsNone(deployment.coordinator)
        self.assertEqual(deployment.targets, ())
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "generation-zero"):
            quadlets.render_target_bundle(
                deployment, "TARGET_TEST_MESH_001", self.root / "target.local.d"
            )

    def test_init_and_render_refuse_overwrite(self) -> None:
        self._write()
        with self.assertRaisesRegex(quadlets.MeshRenderError, "refuses to overwrite"):
            quadlets.initialize_config(self.config)

        self._write(self._targets_payload(), path=self.targets_config)
        deployment = quadlets.load_deployment(self.config, self.targets_config)
        output = self.root / "target.local.d"
        quadlets.render_target_bundle(deployment, "TARGET_TEST_MESH_001", output)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "refuses to overwrite"):
            quadlets.render_target_bundle(deployment, "TARGET_TEST_MESH_001", output)

    def test_private_config_permissions_links_and_absolute_path_are_enforced(self) -> None:
        self._write()
        self.config.chmod(0o640)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "group or other"):
            quadlets.load_deployment(self.config)

        self.config.chmod(0o600)
        hardlink = self.root / "hardlink.local.json"
        os.link(self.config, hardlink)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "hard-linked"):
            quadlets.load_deployment(self.config)
        hardlink.unlink()

        target = self.root / "target.local.json"
        self.config.rename(target)
        self.config.symlink_to(target)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "unsafe"):
            quadlets.load_deployment(self.config)

        with self.assertRaisesRegex(quadlets.MeshRenderError, "absolute"):
            quadlets.load_deployment("relative.local.json")

    def test_private_paths_inside_git_must_be_ignored_and_untracked(self) -> None:
        worktree = self.root / "worktree"
        worktree.mkdir(mode=0o700)
        subprocess.run(
            ["git", "init", "-q", str(worktree)], check=True, capture_output=True
        )
        config = worktree / "podman-mesh.local.json"
        self._write(path=config)

        with self.assertRaisesRegex(quadlets.MeshRenderError, "must be ignored"):
            quadlets.load_deployment(config)

        (worktree / ".gitignore").write_text("*.local.json\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(worktree), "add", ".gitignore"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "user.name=Synthetic Test",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-qm",
                "add ignore contract",
            ],
            check=True,
            capture_output=True,
        )
        inert = self._payload()
        inert["deployment_generation"] = 0
        inert["target_set_id"] = None
        inert["coordinator"] = None
        inert["targets"] = []
        self._write(inert, path=config)
        quadlets.load_deployment(config)
        subprocess.run(
            ["git", "-C", str(worktree), "add", "-f", config.name],
            check=True,
            capture_output=True,
        )
        with self.assertRaisesRegex(quadlets.MeshRenderError, "must not be tracked"):
            quadlets.load_deployment(config)

    def test_git_index_flags_cannot_hide_a_changed_ignore_rule(self) -> None:
        for index, flag in enumerate(("--assume-unchanged", "--skip-worktree")):
            with self.subTest(flag=flag):
                worktree = self.root / f"flagged-worktree-{index}"
                worktree.mkdir(mode=0o700)
                subprocess.run(
                    ["git", "init", "-q", str(worktree)],
                    check=True,
                    capture_output=True,
                )
                ignore = worktree / ".gitignore"
                ignore.write_text("*.local.json\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(worktree), "add", ".gitignore"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(worktree),
                        "-c",
                        "user.name=Synthetic Test",
                        "-c",
                        "user.email=synthetic@example.invalid",
                        "commit",
                        "-qm",
                        "add ignore contract",
                    ],
                    check=True,
                    capture_output=True,
                )
                config = worktree / "podman-mesh.local.json"
                inert = self._payload()
                inert["deployment_generation"] = 0
                inert["target_set_id"] = None
                inert["coordinator"] = None
                inert["targets"] = []
                self._write(inert, path=config)
                quadlets.load_deployment(config)

                subprocess.run(
                    ["git", "-C", str(worktree), "update-index", flag, ".gitignore"],
                    check=True,
                    capture_output=True,
                )
                ignore.write_text("podman-mesh.local.json\n", encoding="utf-8")

                with self.assertRaisesRegex(
                    quadlets.MeshRenderError,
                    "skip-worktree or assume-unchanged",
                ):
                    quadlets.load_deployment(config)

    def test_duplicate_and_unknown_json_keys_are_rejected(self) -> None:
        self.config.write_text(
            '{"schema_version":1,"schema_version":1}\n', encoding="utf-8"
        )
        self.config.chmod(0o600)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "duplicate JSON key"):
            quadlets.load_deployment(self.config)

        payload = self._payload()
        payload["unexpected"] = True
        with self.assertRaisesRegex(quadlets.MeshRenderError, "unknown unexpected"):
            self._load(payload)

    def test_generation_zero_is_exactly_inert(self) -> None:
        payload = self._payload()
        payload["deployment_generation"] = 0
        with self.assertRaisesRegex(quadlets.MeshRenderError, "must remain inert"):
            self._load(payload)

        payload["coordinator"] = None
        payload["target_set_id"] = None
        payload["targets"] = []
        deployment = self._load(payload)
        self.assertEqual(deployment.deployment_generation, 0)

    def test_active_deployment_requires_coordinator_and_targets(self) -> None:
        payload = self._payload()
        payload["coordinator"] = None
        with self.assertRaisesRegex(quadlets.MeshRenderError, "one coordinator"):
            self._load(payload)

        payload = self._payload()
        payload["targets"] = []
        with self.assertRaisesRegex(quadlets.MeshRenderError, "at least 3"):
            self._load(payload)

        payload = self._payload(targets=2)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "at least 3"):
            self._load(payload)

    def test_active_deployment_requires_authoritative_targets(self) -> None:
        self._write(self._payload())
        with self.assertRaisesRegex(quadlets.MeshRenderError, "authoritative"):
            quadlets.load_deployment(self.config)

    def test_authoritative_topology_must_match_exactly_and_use_explicit_high_port(
        self,
    ) -> None:
        deployment_payload = self._payload()
        self._write(deployment_payload)
        targets_payload = self._targets_payload(deployment_payload)

        targets_payload["target_sets"][0]["targets"][0]["mesh_address"] = (  # type: ignore[index]
            "10.88.0.99"
        )
        self._write(targets_payload, path=self.targets_config)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "do not exactly match"):
            quadlets.load_deployment(self.config, self.targets_config)

        targets_payload = self._targets_payload(deployment_payload)
        del targets_payload["target_sets"][0]["targets"][0]["sftp_port"]  # type: ignore[index]
        self._write(targets_payload, path=self.targets_config)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "missing sftp_port"):
            quadlets.load_deployment(self.config, self.targets_config)

        targets_payload = self._targets_payload(deployment_payload)
        targets_payload["target_sets"][0]["targets"][0]["sftp_port"] = 22  # type: ignore[index]
        self._write(targets_payload, path=self.targets_config)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "1024"):
            quadlets.load_deployment(self.config, self.targets_config)

        targets_payload = self._targets_payload(deployment_payload)
        targets_payload["target_generation"] = 0
        self._write(targets_payload, path=self.targets_config)
        with self.assertRaisesRegex(quadlets.MeshRenderError, "at least one"):
            quadlets.load_deployment(self.config, self.targets_config)

        targets_payload = self._targets_payload(deployment_payload)
        self._write(targets_payload, path=self.targets_config)
        majority_two = quadlets.load_deployment(self.config, self.targets_config)
        targets_payload["target_sets"][0]["required_acks"] = 3  # type: ignore[index]
        self._write(targets_payload, path=self.targets_config)
        majority_three = quadlets.load_deployment(self.config, self.targets_config)
        self.assertNotEqual(
            majority_two.target_topology_sha256,
            majority_three.target_topology_sha256,
        )

        targets_payload = self._targets_payload(deployment_payload)
        self._write(targets_payload, path=self.targets_config)
        original_document = quadlets.load_deployment(
            self.config, self.targets_config
        )
        targets_payload["target_sets"][0]["targets"][0]["repository_file"] = (  # type: ignore[index]
            "/synthetic/rotated/repository"
        )
        self._write(targets_payload, path=self.targets_config)
        rotated_document = quadlets.load_deployment(
            self.config, self.targets_config
        )
        self.assertEqual(
            original_document.target_topology_sha256,
            rotated_document.target_topology_sha256,
        )
        self.assertNotEqual(
            original_document.target_document_sha256,
            rotated_document.target_document_sha256,
        )

    def test_render_requires_exactly_one_selected_target(self) -> None:
        deployment = self._load()
        with self.assertRaisesRegex(quadlets.MeshRenderError, "exactly one"):
            quadlets.render_target_bundle(
                deployment, "TARGET_TEST_MESH_UNKNOWN", self.root / "unknown.local.d"
            )

    def test_images_must_be_digest_pinned_and_names_must_be_safe(self) -> None:
        payload = self._payload()
        payload["coordinator"]["image"] = "example.invalid/sidecar:latest"  # type: ignore[index]
        with self.assertRaisesRegex(quadlets.MeshRenderError, "repo@sha256"):
            self._load(payload)

        payload = self._payload()
        payload["targets"][0]["unit_name"] = "Unsafe Unit"  # type: ignore[index]
        with self.assertRaisesRegex(quadlets.MeshRenderError, "lowercase"):
            self._load(payload)

        payload = self._payload()
        payload["coordinator"]["image"] = "sha256:" + "3" * 64  # type: ignore[index]
        payload["targets"][0]["image"] = "sha256:" + "4" * 64  # type: ignore[index]
        deployment = self._load(payload)
        self.assertEqual(deployment.coordinator.image, "sha256:" + "3" * 64)  # type: ignore[union-attr]

    def test_only_high_explicit_ports_and_rfc1918_bind_addresses_are_accepted(self) -> None:
        mutations: tuple[tuple[str, object, str], ...] = (
            ("mesh_address", "0.0.0.0", "RFC1918"),
            ("mesh_address", "127.0.0.1", "RFC1918"),
            ("mesh_address", "203.0.113.10", "RFC1918"),
            ("mesh_address", "10.0.0.0", "unicast"),
            ("published_port", 22, "1024"),
            ("published_port", True, "1024"),
            ("container_port", 22, "1024"),
        )
        for key, value, message in mutations:
            with self.subTest(key=key, value=value):
                payload = self._payload()
                payload["targets"][0][key] = value  # type: ignore[index]
                with self.assertRaisesRegex(quadlets.MeshRenderError, message):
                    self._load(payload)

    def test_targets_must_be_sorted_and_security_boundaries_distinct(self) -> None:
        payload = self._payload()
        payload["targets"] = list(reversed(payload["targets"]))  # type: ignore[arg-type]
        with self.assertRaisesRegex(quadlets.MeshRenderError, "sorted"):
            self._load(payload)

        duplicate_fields = (
            "target_id",
            "failure_domain",
            "mesh_address",
            "unit_name",
            "container_name",
            "repository_volume",
        )
        for key in duplicate_fields:
            with self.subTest(key=key):
                payload = self._payload()
                payload["targets"][1][key] = copy.deepcopy(  # type: ignore[index]
                    payload["targets"][0][key]  # type: ignore[index]
                )
                if key == "target_id":
                    expected = "distinct target IDs"
                elif key == "failure_domain":
                    expected = "distinct failure domains"
                elif key == "mesh_address":
                    expected = "distinct mesh addresses"
                elif key == "unit_name":
                    expected = "distinct unit names"
                elif key == "container_name":
                    expected = "distinct container names"
                else:
                    expected = "distinct repository volumes"
                with self.assertRaisesRegex(quadlets.MeshRenderError, expected):
                    self._load(payload)

        payload = self._payload()
        payload["targets"][1]["host_key_secret"] = (  # type: ignore[index]
            payload["targets"][0]["authorized_keys_secret"]  # type: ignore[index]
        )
        with self.assertRaisesRegex(quadlets.MeshRenderError, "distinct secret names"):
            self._load(payload)

    def test_coordinator_runtime_names_cannot_collide_with_targets(self) -> None:
        for key in ("unit_name", "container_name"):
            with self.subTest(key=key):
                payload = self._payload()
                payload["coordinator"][key] = payload["targets"][0][key]  # type: ignore[index]
                with self.assertRaisesRegex(quadlets.MeshRenderError, "distinct"):
                    self._load(payload)


if __name__ == "__main__":
    unittest.main()
