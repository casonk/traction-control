"""Focused regressions for the three-level portfolio sidecar runtime."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import portfolio_sidecar as sidecar  # noqa: E402


class PortfolioSidecarTests(unittest.TestCase):
    REPOSITORY_ID = "R_public_sidecar_synthetic"
    REPOSITORY_SLUG = "synthetic-owner/public-sidecar-synthetic"
    DATASET_ID = "dataset-public-sidecar"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.portfolio = self.root / "portfolio"
        self.checkout = self.portfolio / "public-sidecar"
        self.control = self.root / "control"
        self.secrets = self.root / "secrets"
        for directory in (
            self.portfolio,
            self.checkout,
            self.control,
            self.secrets,
        ):
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        self.private_path = self.control / "private.local.json"
        self.public_path = self.control / "public.local.json"
        self.catalog_path = self.control / "portfolio.local.json"
        self.policy_path = self.control / "sidecar-policy.local.json"
        self.targets_path = self.control / "sidecar-targets.local.json"
        self.state_path = self.control / "sidecar-state.local.json"
        self.inventory_path = self.control / "sidecar-inventory.local.json"
        self.known_hosts = self.secrets / "known_hosts"
        self.fake_restic = self.root / "fake-restic"
        self.fake_ssh = self.root / "fake-ssh"
        self._initialize_checkout()
        self._write_registry_and_catalog()
        self._write_policy(tier="hosted-encrypted")
        self._write_targets(tier="hosted-encrypted", failures=())
        self._write_secret(self.known_hosts, "synthetic known-host key\n")
        self._write_fake_executables()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return subprocess.run(
            ["git", "-C", str(self.checkout), *arguments],
            check=check,
            capture_output=True,
            text=True,
            env=environment,
        )

    def _initialize_checkout(self) -> None:
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Synthetic Test")
        self._git("config", "user.email", "synthetic@example.invalid")
        self._git(
            "remote",
            "add",
            "origin",
            f"https://github.com/{self.REPOSITORY_SLUG}.git",
        )
        (self.checkout / ".gitignore").write_text(
            "sidecar-data/\n",
            encoding="utf-8",
        )
        (self.checkout / "README.md").write_text("# public code\n", encoding="utf-8")
        self._git("add", ".gitignore", "README.md")
        self._git("commit", "-qm", "initial")
        selected = self.checkout / "sidecar-data"
        selected.mkdir()
        (selected / "alpha.txt").write_text("alpha private data\n", encoding="utf-8")
        nested = selected / "nested"
        nested.mkdir()
        (nested / "beta.bin").write_bytes(b"\x00\x01synthetic\n")

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _write_secret(self, path: Path, value: str) -> None:
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)

    def _write_registry_and_catalog(self) -> None:
        common = {
            "schema_version": 1,
            "registry_id": "sidecar-synthetic-registry",
            "generation": 1,
        }
        self._write_json(
            self.private_path,
            {**common, "visibility": "private", "repositories": []},
        )
        self._write_json(
            self.public_path,
            {
                **common,
                "visibility": "public",
                "repositories": [
                    {"id": self.REPOSITORY_ID, "slug": self.REPOSITORY_SLUG}
                ],
            },
        )
        self._write_json(
            self.catalog_path,
            {
                "schema_version": 1,
                "registry_id": "sidecar-synthetic-registry",
                "registry_generation": 1,
                "catalog_generation": 1,
                "repositories": [
                    {
                        "repository_id": self.REPOSITORY_ID,
                        "relative_path": "public-sidecar",
                        "lifecycle": "active",
                        "sync_policy": "manual",
                        "desired_presence": "checkout",
                    }
                ],
            },
        )

    def _write_policy(
        self,
        *,
        tier: str,
        selector: str = "sidecar-data",
        max_files: int = 20,
        max_total_bytes: int = 1024 * 1024,
        target_set_id: str | None = None,
    ) -> None:
        self._write_json(
            self.policy_path,
            {
                "schema_version": 1,
                "registry_id": "sidecar-synthetic-registry",
                "registry_generation": 1,
                "policy_generation": 1,
                "datasets": [
                    {
                        "dataset_id": self.DATASET_ID,
                        "repository_id": self.REPOSITORY_ID,
                        "selectors": [selector],
                        "tier": tier,
                        "adapter": "filesystem-static",
                        "max_files": max_files,
                        "max_total_bytes": max_total_bytes,
                        "target_set_id": target_set_id or f"targets-{tier}",
                    }
                ],
            },
        )

    def _write_targets(self, *, tier: str, failures: tuple[int, ...]) -> None:
        if tier == "hosted-encrypted":
            target_count = 1
            required_acks = 1
        else:
            target_count = 3
            required_acks = 2
        targets: list[dict[str, object]] = []
        for index in range(1, target_count + 1):
            repository_file = self.secrets / f"repository-{index}.txt"
            password_file = self.secrets / f"password-{index}.txt"
            identity_file = self.secrets / f"identity-{index}.txt"
            if tier == "mesh-only":
                host = f"10.44.0.{index}"
                mesh_address: str | None = host
            else:
                host = f"backup-{index}.example.invalid"
                mesh_address = None
            suffix = f"/fail-{index}" if index in failures else f"/repo-{index}"
            self._write_secret(
                repository_file,
                f"sftp:backup@{host}:{suffix}\n",
            )
            self._write_secret(password_file, f"synthetic-password-{index}\n")
            self._write_secret(
                identity_file,
                f"synthetic-ssh-identity-{index}\n",
            )
            targets.append(
                {
                    "target_id": f"target-{index}",
                    "repository_file": str(repository_file.resolve()),
                    "password_file": str(password_file.resolve()),
                    "identity_file": str(identity_file.resolve()),
                    "mesh_address": mesh_address,
                    "failure_domain": f"failure-domain-{index}",
                }
            )
        self._write_json(
            self.targets_path,
            {
                "schema_version": 1,
                "registry_id": "sidecar-synthetic-registry",
                "registry_generation": 1,
                "target_generation": 1,
                "target_sets": [
                    {
                        "target_set_id": f"targets-{tier}",
                        "tier": tier,
                        "required_acks": required_acks,
                        "targets": targets,
                    }
                ],
            },
        )

    def _write_fake_executables(self) -> None:
        self.fake_restic.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, json, os, pathlib, sys\n"
            "arguments = sys.argv[1:]\n"
            "options = [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == '-o']\n"
            "sftp_commands = [value for value in options if value.startswith('sftp.command=')]\n"
            "sftp_args = [value for value in options if value.startswith('sftp.args=')]\n"
            "if len(sftp_commands) != 1 or sftp_args:\n"
            "    print('invalid restic SFTP option contract', file=sys.stderr)\n"
            "    raise SystemExit(91)\n"
            "ssh_command = sftp_commands[0].removeprefix('sftp.command=').split()\n"
            "if ssh_command[-2:] != ['-s', 'sftp']:\n"
            "    print('incomplete restic SFTP command', file=sys.stderr)\n"
            "    raise SystemExit(92)\n"
            "raw_files = sys.stdin.buffer.read()\n"
            "repository_file = pathlib.Path(arguments[arguments.index('--repository-file') + 1])\n"
            "repository = repository_file.read_text(encoding='utf-8').strip()\n"
            "log = pathlib.Path(sys.argv[0] + '.log')\n"
            "with log.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps({"
            "'argv': arguments, 'stdin_hex': raw_files.hex(), 'cwd': os.getcwd(), "
            "'home': os.environ.get('HOME'), 'ssh_auth_sock': os.environ.get('SSH_AUTH_SOCK')"
            "}) + '\\n')\n"
            "if '/fail-' in repository:\n"
            "    print('RAW-RESTIC-SECRET-FAILURE', file=sys.stderr)\n"
            "    raise SystemExit(17)\n"
            "snapshot = hashlib.sha256(repository.encode() + raw_files).hexdigest()\n"
            "print(json.dumps({'message_type': 'status', 'current_files': ['RAW-SOURCE-PATH']}))\n"
            "print(json.dumps({'message_type': 'summary', 'snapshot_id': snapshot}))\n",
            encoding="utf-8",
        )
        self.fake_restic.chmod(0o700)
        self.fake_ssh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.fake_ssh.chmod(0o700)

    def _arguments(self, command: str) -> list[str]:
        arguments = [
            command,
            "--private",
            str(self.private_path),
            "--public",
            str(self.public_path),
            "--catalog",
            str(self.catalog_path),
            "--portfolio-root",
            str(self.portfolio),
            "--policy",
            str(self.policy_path),
            "--targets",
            str(self.targets_path),
            "--state",
            str(self.state_path),
        ]
        if command in {"backup", "drill"}:
            arguments.extend(
                (
                    "--restic",
                    str(self.fake_restic),
                    "--ssh",
                    str(self.fake_ssh),
                    "--known-hosts",
                    str(self.known_hosts),
                )
            )
        if command == "drill":
            arguments.extend(
                ("--evidence", str(self.control / "drill-evidence.local.json"))
            )
        return arguments

    def _main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = sidecar.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def _init_state(self) -> None:
        result, _stdout, stderr = self._main(self._arguments("init-state"))
        self.assertEqual(result, 0, stderr)

    def _init_config_arguments(
        self,
        policy_path: Path | None = None,
        targets_path: Path | None = None,
    ) -> list[str]:
        return [
            "init-config",
            "--private",
            str(self.private_path),
            "--public",
            str(self.public_path),
            "--policy",
            str(policy_path or self.policy_path),
            "--targets",
            str(targets_path or self.targets_path),
        ]

    def _inventory_arguments(self, *, show_paths: bool = False) -> list[str]:
        arguments = [
            "inventory-candidates",
            "--private",
            str(self.private_path),
            "--public",
            str(self.public_path),
            "--catalog",
            str(self.catalog_path),
            "--portfolio-root",
            str(self.portfolio),
            "--output",
            str(self.inventory_path),
        ]
        if show_paths:
            arguments.append("--show-paths")
        return arguments

    def _read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _read_restic_log(self) -> list[dict[str, object]]:
        log_path = Path(str(self.fake_restic) + ".log")
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]

    def test_init_config_creates_a_secure_inert_registry_bound_pair(self) -> None:
        control = self.checkout / "sidecar-data" / "bootstrap-control"
        control.mkdir(mode=0o755)
        policy_path = control / "policy.local.json"
        targets_path = control / "targets.local.json"
        state_path = control / "state.local.json"

        result, stdout, stderr = self._main(
            self._init_config_arguments(policy_path, targets_path)
        )

        self.assertEqual(result, 0, stderr)
        self.assertIn("0 dataset(s), 0 target set(s)", stdout)
        self.assertIn("no data is protected", stdout)
        self.assertNotIn("sidecar-synthetic-registry", stdout + stderr)
        self.assertNotIn(str(control), stdout + stderr)
        self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(policy_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(targets_path.stat().st_mode), 0o600)
        self.assertFalse(state_path.exists())
        self.assertFalse((control / "credentials").exists())

        lock_path = control / ".portfolio-sidecar.lock"
        lock_metadata = lock_path.lstat()
        self.assertTrue(stat.S_ISREG(lock_metadata.st_mode))
        self.assertEqual(stat.S_IMODE(lock_metadata.st_mode), 0o600)
        self.assertEqual(lock_metadata.st_nlink, 1)
        for path in (policy_path, targets_path, lock_path):
            relative_path = path.relative_to(self.checkout).as_posix()
            self.assertEqual(
                self._git("check-ignore", "--no-index", "--", relative_path).returncode,
                0,
            )

        policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
        targets_payload = json.loads(targets_path.read_text(encoding="utf-8"))
        self.assertEqual(
            policy_payload,
            {
                "schema_version": 1,
                "registry_id": "sidecar-synthetic-registry",
                "registry_generation": 1,
                "policy_generation": 0,
                "datasets": [],
            },
        )
        self.assertEqual(
            targets_payload,
            {
                "schema_version": 1,
                "registry_id": "sidecar-synthetic-registry",
                "registry_generation": 1,
                "target_generation": 0,
                "target_sets": [],
            },
        )
        pair = sidecar.visibility.load_pair(self.private_path, self.public_path)
        policy = sidecar.load_policy(policy_path, pair)
        targets = sidecar.load_targets(targets_path, pair)
        sidecar.validate_policy_targets(policy, targets)

    def test_init_config_refuses_to_overwrite_either_half(self) -> None:
        for existing_name in ("policy", "targets"):
            with self.subTest(existing_name=existing_name):
                self.policy_path.unlink(missing_ok=True)
                self.targets_path.unlink(missing_ok=True)
                existing = (
                    self.policy_path
                    if existing_name == "policy"
                    else self.targets_path
                )
                other = (
                    self.targets_path
                    if existing_name == "policy"
                    else self.policy_path
                )
                self._write_secret(existing, "do-not-overwrite\n")
                before = existing.read_bytes()

                result, _stdout, stderr = self._main(
                    self._init_config_arguments()
                )

                self.assertEqual(result, 2)
                self.assertIn("refuses to overwrite", stderr)
                self.assertEqual(existing.read_bytes(), before)
                self.assertFalse(other.exists())

    def test_init_config_rolls_back_if_pair_publication_is_interrupted(self) -> None:
        self.policy_path.unlink()
        self.targets_path.unlink()
        original_write = sidecar._write_new_private_json

        def fail_second_write(
            path: Path,
            payload: dict[str, object],
            *,
            label: str,
            directory_descriptor: int,
        ) -> sidecar._CreatedPrivateFile:
            if label == "sidecar targets":
                raise sidecar.SidecarError("synthetic publication failure")
            return original_write(
                path,
                payload,
                label=label,
                directory_descriptor=directory_descriptor,
            )

        with mock.patch.object(
            sidecar,
            "_write_new_private_json",
            side_effect=fail_second_write,
        ):
            result, _stdout, stderr = self._main(self._init_config_arguments())

        self.assertEqual(result, 2)
        self.assertIn("synthetic publication failure", stderr)
        self.assertFalse(self.policy_path.exists())
        self.assertFalse(self.targets_path.exists())

    def test_init_config_rejects_unignored_destinations_without_artifacts(self) -> None:
        destination = self.checkout / "bootstrap-control"
        destination.mkdir(mode=0o755)
        policy_path = destination / "policy.local.json"
        targets_path = destination / "targets.local.json"

        result, _stdout, stderr = self._main(
            self._init_config_arguments(policy_path, targets_path)
        )

        self.assertEqual(result, 2)
        self.assertIn("tracked, unchanged .gitignore", stderr)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
        self.assertFalse(policy_path.exists())
        self.assertFalse(targets_path.exists())
        self.assertFalse((destination / ".portfolio-sidecar.lock").exists())

    def test_init_config_rejects_local_only_ignore_rules(self) -> None:
        destination = self.checkout / "bootstrap-control"
        destination.mkdir(mode=0o755)
        (self.checkout / ".git" / "info" / "exclude").write_text(
            "bootstrap-control/\n",
            encoding="utf-8",
        )

        result, _stdout, stderr = self._main(
            self._init_config_arguments(
                destination / "policy.local.json",
                destination / "targets.local.json",
            )
        )

        self.assertEqual(result, 2)
        self.assertIn("tracked, unchanged .gitignore", stderr)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
        self.assertEqual(tuple(destination.iterdir()), ())

    def test_init_config_rejects_uncommitted_ignore_rules(self) -> None:
        destination = self.checkout / "bootstrap-control"
        destination.mkdir(mode=0o755)
        with (self.checkout / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("bootstrap-control/\n")

        result, _stdout, stderr = self._main(
            self._init_config_arguments(
                destination / "policy.local.json",
                destination / "targets.local.json",
            )
        )

        self.assertEqual(result, 2)
        self.assertIn("tracked, unchanged .gitignore", stderr)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
        self.assertEqual(tuple(destination.iterdir()), ())

    def test_init_config_fails_closed_for_a_broken_git_worktree(self) -> None:
        worktree = self.root / "broken-worktree"
        destination = worktree / "bootstrap-control"
        worktree.mkdir(mode=0o755)
        destination.mkdir(mode=0o755)
        (worktree / ".git").write_text(
            "gitdir: missing-control-directory\n",
            encoding="utf-8",
        )

        result, _stdout, stderr = self._main(
            self._init_config_arguments(
                destination / "policy.local.json",
                destination / "targets.local.json",
            )
        )

        self.assertEqual(result, 2)
        self.assertIn("cannot validate", stderr)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
        self.assertEqual(tuple(destination.iterdir()), ())

    def test_init_config_rejects_malformed_paths_without_a_traceback(self) -> None:
        arguments = self._init_config_arguments()
        arguments[arguments.index("--policy") + 1] += "\0invalid"

        result, stdout, stderr = self._main(arguments)

        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("control path is invalid", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_init_config_interrupt_rolls_back_without_a_traceback(self) -> None:
        self.policy_path.unlink()
        self.targets_path.unlink()
        original_write = sidecar._write_new_private_json

        def interrupt_second_write(
            path: Path,
            payload: dict[str, object],
            *,
            label: str,
            directory_descriptor: int,
        ) -> sidecar._CreatedPrivateFile:
            if label == "sidecar targets":
                raise KeyboardInterrupt
            return original_write(
                path,
                payload,
                label=label,
                directory_descriptor=directory_descriptor,
            )

        with mock.patch.object(
            sidecar,
            "_write_new_private_json",
            side_effect=interrupt_second_write,
        ):
            result, stdout, stderr = self._main(self._init_config_arguments())

        self.assertEqual(result, 130)
        self.assertEqual(stdout, "")
        self.assertIn("operation interrupted", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertFalse(self.policy_path.exists())
        self.assertFalse(self.targets_path.exists())

    def test_init_config_rollback_preserves_a_replacement_file(self) -> None:
        self.policy_path.unlink()
        self.targets_path.unlink()
        original_write = sidecar._write_new_private_json
        replacement = self.control / "replacement.local.json"

        def replace_before_failure(
            path: Path,
            payload: dict[str, object],
            *,
            label: str,
            directory_descriptor: int,
        ) -> sidecar._CreatedPrivateFile:
            if label == "sidecar targets":
                self._write_secret(replacement, "replacement-must-survive\n")
                os.replace(replacement, self.policy_path)
                raise sidecar.SidecarError("synthetic publication failure")
            return original_write(
                path,
                payload,
                label=label,
                directory_descriptor=directory_descriptor,
            )

        with mock.patch.object(
            sidecar,
            "_write_new_private_json",
            side_effect=replace_before_failure,
        ):
            result, _stdout, stderr = self._main(self._init_config_arguments())

        self.assertEqual(result, 2)
        self.assertIn("could not roll back", stderr)
        self.assertEqual(
            self.policy_path.read_text(encoding="utf-8"),
            "replacement-must-survive\n",
        )
        self.assertFalse(self.targets_path.exists())

    def test_init_config_link_stat_race_preserves_a_replacement_file(self) -> None:
        self.policy_path.unlink()
        self.targets_path.unlink()
        replacement = self.control / "replacement.local.json"
        original_link = sidecar.os.link

        def replace_after_link(
            source: str,
            destination: str,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> None:
            original_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            self._write_secret(replacement, "link-race-must-survive\n")
            os.replace(replacement, self.policy_path)

        with mock.patch.object(
            sidecar.os,
            "link",
            side_effect=replace_after_link,
        ):
            result, _stdout, stderr = self._main(self._init_config_arguments())

        self.assertEqual(result, 2)
        self.assertIn("changed file", stderr)
        self.assertEqual(
            self.policy_path.read_text(encoding="utf-8"),
            "link-race-must-survive\n",
        )
        self.assertFalse(self.targets_path.exists())

    def test_inventory_candidates_is_metadata_only_bound_and_redacted(self) -> None:
        policy_before = self.policy_path.read_bytes()
        targets_before = self.targets_path.read_bytes()
        selected_files = (
            self.checkout / "sidecar-data" / "alpha.txt",
            self.checkout / "sidecar-data" / "nested" / "beta.bin",
        )
        selected_files[0].chmod(0o000)

        result, stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 0, stderr)
        self.assertIn("advisory", stdout)
        self.assertIn("inspected=1", stdout)
        self.assertIn("candidates=1", stdout)
        self.assertNotIn(self.REPOSITORY_ID, stdout + stderr)
        self.assertNotIn("sidecar-data", stdout + stderr)
        self.assertNotIn(str(self.inventory_path), stdout + stderr)
        self.assertEqual(self.policy_path.read_bytes(), policy_before)
        self.assertEqual(self.targets_path.read_bytes(), targets_before)
        metadata = self.inventory_path.lstat()
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        lock_metadata = (self.control / ".portfolio-sidecar.lock").lstat()
        self.assertEqual(stat.S_IMODE(lock_metadata.st_mode), 0o600)
        self.assertEqual(lock_metadata.st_nlink, 1)
        payload = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["registry_id"], "sidecar-synthetic-registry")
        self.assertEqual(payload["registry_generation"], 1)
        self.assertEqual(payload["catalog_generation"], 1)
        self.assertIs(payload["advisory_only"], True)
        self.assertEqual(len(payload["repositories"]), 1)
        repository = payload["repositories"][0]
        self.assertEqual(repository["repository_id"], self.REPOSITORY_ID)
        self.assertEqual(repository["status"], "inspected")
        self.assertEqual(
            repository["candidates"],
            [
                {
                    "selector": "sidecar-data",
                    "kind": "directory",
                    "file_count": 2,
                    "total_bytes": sum(path.stat().st_size for path in selected_files),
                }
            ],
        )
        first_inode = metadata.st_ino

        result, stdout, stderr = self._main(
            self._inventory_arguments(show_paths=True)
        )

        self.assertEqual(result, 0, stderr)
        self.assertIn(self.REPOSITORY_ID, stdout)
        self.assertIn("sidecar-data", stdout)
        self.assertNotEqual(self.inventory_path.lstat().st_ino, first_inode)
        self.assertEqual(self.policy_path.read_bytes(), policy_before)
        self.assertEqual(self.targets_path.read_bytes(), targets_before)

    def test_inventory_candidates_excludes_control_cache_build_and_locks(self) -> None:
        with (self.checkout / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write(
                "__pycache__/\n"
                "build/\n"
                "config/portfolio-sidecar/*.local.*\n"
                ".portfolio-sidecar/\n"
                "*.lock\n"
            )
        self._git("add", ".gitignore")
        self._git("commit", "-qm", "add synthetic ignored metadata")
        generated = self.checkout / "scripts" / "__pycache__"
        generated.mkdir(parents=True)
        (generated / "module.pyc").write_bytes(b"generated")
        build = self.checkout / "build"
        build.mkdir()
        (build / "output.bin").write_bytes(b"generated")
        sidecar_control = self.checkout / "config" / "portfolio-sidecar"
        sidecar_control.mkdir(parents=True)
        (sidecar_control / "policy.local.json").write_text(
            "private control\n",
            encoding="utf-8",
        )
        internal_control = self.checkout / ".portfolio-sidecar"
        internal_control.mkdir()
        (internal_control / "manifest.json").write_text(
            "private control\n",
            encoding="utf-8",
        )
        (self.checkout / "operation.lock").write_text("lock\n", encoding="utf-8")

        result, _stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 0, stderr)
        payload = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        repository = payload["repositories"][0]
        self.assertEqual(
            [candidate["selector"] for candidate in repository["candidates"]],
            ["sidecar-data"],
        )
        exclusions = repository["excluded_counts"]
        self.assertEqual(exclusions["cache-or-build"], 2)
        self.assertEqual(exclusions["sidecar-control"], 2)
        self.assertEqual(exclusions["lock-or-temporary"], 1)

    def test_inventory_candidates_records_dirty_checkout_as_unready(self) -> None:
        policy_before = self.policy_path.read_bytes()
        targets_before = self.targets_path.read_bytes()
        (self.checkout / "README.md").write_text(
            "# dirty public code\n",
            encoding="utf-8",
        )

        result, stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 0, stderr)
        self.assertIn("unready=1", stdout)
        self.assertNotIn(self.REPOSITORY_ID, stdout + stderr)
        payload_bytes = self.inventory_path.read_bytes()
        payload = json.loads(payload_bytes)
        repository = payload["repositories"][0]
        self.assertEqual(repository["status"], "unready")
        self.assertEqual(repository["candidates"], [])
        self.assertEqual(repository["excluded_counts"]["checkout-unready"], 1)
        self.assertNotIn(b"sidecar-data", payload_bytes)
        self.assertEqual(self.policy_path.read_bytes(), policy_before)
        self.assertEqual(self.targets_path.read_bytes(), targets_before)

    def test_inventory_candidates_refuses_input_aliases_and_unrelated_output(
        self,
    ) -> None:
        for protected in (
            self.private_path,
            self.public_path,
            self.catalog_path,
        ):
            with self.subTest(protected=protected.name):
                before = protected.read_bytes()
                arguments = self._inventory_arguments()
                arguments[arguments.index("--output") + 1] = str(protected)

                result, _stdout, stderr = self._main(arguments)

                self.assertEqual(result, 2)
                self.assertIn("must not alias", stderr)
                self.assertEqual(protected.read_bytes(), before)

        self._write_secret(self.inventory_path, "unrelated private document\n")
        before = self.inventory_path.read_bytes()

        result, _stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 2)
        self.assertIn("inventory output", stderr)
        self.assertEqual(self.inventory_path.read_bytes(), before)

    def test_inventory_candidates_first_publication_never_overwrites_a_race(
        self,
    ) -> None:
        original_link = sidecar.os.link
        collided = False

        def publish_after_collision(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            nonlocal collided
            if destination == self.inventory_path.name and not collided:
                collided = True
                self._write_secret(
                    self.inventory_path,
                    "concurrent file must survive\n",
                )
            original_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(sidecar.os, "link", side_effect=publish_after_collision):
            result, _stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 2)
        self.assertTrue(collided)
        self.assertIn("refuses to overwrite", stderr)
        self.assertEqual(
            self.inventory_path.read_text(encoding="utf-8"),
            "concurrent file must survive\n",
        )

    def test_inventory_candidates_never_inspects_private_registry_entries(self) -> None:
        private_id = "R_private_sidecar_synthetic"
        private_slug = "synthetic-owner/private-sidecar-synthetic"
        common = {
            "schema_version": 1,
            "registry_id": "sidecar-synthetic-registry",
            "generation": 1,
        }
        self._write_json(
            self.private_path,
            {
                **common,
                "visibility": "private",
                "repositories": [{"id": private_id, "slug": private_slug}],
            },
        )
        self._write_json(
            self.catalog_path,
            {
                "schema_version": 1,
                "registry_id": "sidecar-synthetic-registry",
                "registry_generation": 1,
                "catalog_generation": 1,
                "repositories": [
                    {
                        "repository_id": private_id,
                        "relative_path": "private-sidecar",
                        "lifecycle": "active",
                        "sync_policy": "manual",
                        "desired_presence": "checkout",
                    },
                    {
                        "repository_id": self.REPOSITORY_ID,
                        "relative_path": "public-sidecar",
                        "lifecycle": "active",
                        "sync_policy": "manual",
                        "desired_presence": "checkout",
                    },
                ],
            },
        )
        private_checkout = self.portfolio / "private-sidecar"
        private_checkout.mkdir()
        (private_checkout / ".git").write_text("must not be inspected\n", encoding="utf-8")

        result, _stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 0, stderr)
        payload_bytes = self.inventory_path.read_bytes()
        payload = json.loads(payload_bytes)
        self.assertEqual(len(payload["repositories"]), 1)
        self.assertEqual(
            payload["repositories"][0]["repository_id"],
            self.REPOSITORY_ID,
        )
        self.assertNotIn(private_id.encode(), payload_bytes)

    def test_inventory_candidates_counts_local_only_ignore_as_untrusted(self) -> None:
        local_only = self.checkout / "local-only"
        local_only.mkdir()
        (local_only / "secret.txt").write_text("not read\n", encoding="utf-8")
        (self.checkout / ".git" / "info" / "exclude").write_text(
            "local-only/\n",
            encoding="utf-8",
        )

        result, _stdout, stderr = self._main(self._inventory_arguments())

        self.assertEqual(result, 0, stderr)
        payload = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        repository = payload["repositories"][0]
        self.assertEqual(
            [candidate["selector"] for candidate in repository["candidates"]],
            ["sidecar-data"],
        )
        self.assertEqual(
            repository["excluded_counts"]["untrusted-ignore-rule"],
            1,
        )

    def test_validate_and_plan_are_redacted_unless_paths_are_requested(self) -> None:
        self._init_state()
        result, stdout, stderr = self._main(self._arguments("validate"))
        self.assertEqual(result, 0, stderr)
        self.assertIn("standalone sidecar", stdout)

        result, stdout, stderr = self._main(self._arguments("plan"))
        self.assertEqual(result, 0, stderr)
        self.assertIn("automatic failover requires quorum authority", stdout)
        self.assertIn(self.DATASET_ID, stdout)
        self.assertNotIn("sidecar-data", stdout)
        self.assertNotIn(str(self.checkout), stdout)
        self.assertNotIn("backup-1.example.invalid", stdout)

        detailed = self._arguments("plan") + ["--show-paths"]
        result, stdout, stderr = self._main(detailed)
        self.assertEqual(result, 0, stderr)
        self.assertIn("sidecar-data/alpha.txt", stdout)
        self.assertIn("sidecar-data/nested/beta.bin", stdout)

    def test_backup_passes_only_nul_file_list_and_hardened_ssh_options(self) -> None:
        self._init_state()
        self.known_hosts.chmod(0o644)
        result, stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 0, stderr)
        self.assertNotIn("RAW-SOURCE-PATH", stdout + stderr)
        self.assertNotIn("RAW-RESTIC", stdout + stderr)
        state = self._read_state()
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["manifest_format"], "portable-files-v1")
        self.assertEqual(state["state_generation"], 1)
        dataset = state["datasets"][0]
        self.assertEqual(dataset["sequence"], 1)
        self.assertEqual(len(dataset["replicas"]), 1)
        calls = self._read_restic_log()
        self.assertEqual(len(calls), 1)
        arguments = calls[0]["argv"]
        self.assertNotIn("sidecar-data/alpha.txt", arguments)
        self.assertIn("--no-cache", arguments)
        self.assertIn("--repository-file", arguments)
        self.assertIn("--password-file", arguments)
        repository_argument = arguments[arguments.index("--repository-file") + 1]
        password_argument = arguments[arguments.index("--password-file") + 1]
        self.assertNotEqual(repository_argument, str(self.secrets / "repository-1.txt"))
        self.assertNotEqual(password_argument, str(self.secrets / "password-1.txt"))
        self.assertIn("/spool/run-", repository_argument)
        self.assertIn("/spool/run-", calls[0]["cwd"])
        self.assertNotEqual(calls[0]["cwd"], str(self.checkout))
        self.assertIsNone(calls[0]["home"])
        self.assertIsNone(calls[0]["ssh_auth_sock"])
        option_values = [
            arguments[index + 1]
            for index, value in enumerate(arguments[:-1])
            if value == "-o"
        ]
        self.assertEqual(
            sum(value.startswith("sftp.command=") for value in option_values),
            1,
        )
        self.assertFalse(
            any(value.startswith("sftp.args=") for value in option_values)
        )
        ssh_options = next(
            value.removeprefix("sftp.command=")
            for value in option_values
            if value.startswith("sftp.command=")
        )
        ssh_tokens = ssh_options.split()
        self.assertEqual(ssh_tokens[0], str(self.fake_ssh.resolve()))
        self.assertEqual(
            ssh_tokens[-5:],
            ["-l", "backup", "backup-1.example.invalid", "-s", "sftp"],
        )
        for required in (
            "-F /dev/null",
            "BatchMode=yes",
            "ConnectTimeout=10",
            "ConnectionAttempts=1",
            "StrictHostKeyChecking=yes",
            "UserKnownHostsFile=",
            "GlobalKnownHostsFile=/dev/null",
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "IdentityFile=",
            "PasswordAuthentication=no",
            "ProxyCommand=none",
            "ProxyJump=none",
            "PermitLocalCommand=no",
            "RemoteCommand=none",
        ):
            self.assertIn(required, ssh_options)
        self.assertNotIn(str(self.known_hosts.resolve()), ssh_options)
        self.assertIn("/spool/run-", ssh_options)
        raw_file_list = bytes.fromhex(calls[0]["stdin_hex"])
        self.assertEqual(
            raw_file_list,
            b".portfolio-sidecar\0",
        )
        self.assertEqual(list((self.control / "spool").iterdir()), [])

    def test_state_lock_and_content_hashes_fail_closed_on_credential_rotation(self) -> None:
        self._init_state()
        lock = self.control.resolve() / ".portfolio-sidecar.lock"
        self.assertTrue(lock.is_file())
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        state = self._read_state()
        self.assertRegex(state["policy_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(state["target_sha256"], r"^[0-9a-f]{64}$")

        self._write_secret(
            self.secrets / "password-1.txt",
            "rotated-without-a-new-state-epoch\n",
        )
        result, _stdout, stderr = self._main(self._arguments("validate"))
        self.assertEqual(result, 2)
        self.assertIn("different target or credential content", stderr)

    def test_state_v1_is_refused_without_automatic_migration(self) -> None:
        self._init_state()
        payload = self._read_state()
        payload["schema_version"] = 1
        payload.pop("manifest_format")
        self._write_json(self.state_path, payload)

        result, stdout, stderr = self._main(self._arguments("validate"))

        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("schema_version 1 has no portable restore manifest", stderr)
        self.assertIn("automatic migration is refused", stderr)

    def test_init_state_first_publication_never_overwrites_a_race(self) -> None:
        original_link = sidecar.os.link
        collided = False

        def publish_after_collision(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            nonlocal collided
            if destination == self.state_path.name and not collided:
                collided = True
                self._write_secret(
                    self.state_path,
                    "concurrent state path must survive\n",
                )
            original_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(sidecar.os, "link", side_effect=publish_after_collision):
            result, _stdout, stderr = self._main(self._arguments("init-state"))

        self.assertEqual(result, 2)
        self.assertTrue(collided)
        self.assertIn("refuses to overwrite", stderr)
        self.assertEqual(
            self.state_path.read_text(encoding="utf-8"),
            "concurrent state path must survive\n",
        )

    def test_stale_plaintext_spool_refuses_backup_without_deleting_evidence(self) -> None:
        self._init_state()
        stale = self.control.resolve() / "spool" / "run-stale"
        stale.mkdir(mode=0o700, parents=True)
        stale.chmod(0o700)
        marker = stale / "marker"
        marker.write_text("inspect me\n", encoding="utf-8")
        marker.chmod(0o400)

        result, _stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 2)
        self.assertIn("stale sidecar staging data", stderr)
        self.assertTrue(marker.exists())
        self.assertFalse(Path(str(self.fake_restic) + ".log").exists())

    def test_drill_restores_recorded_committed_snapshot_and_writes_bound_evidence(
        self,
    ) -> None:
        self._init_state()
        initial_state_sha256 = sidecar._canonical_document_hash(self._read_state())
        backup_result, _stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(backup_result, 0, stderr)
        committed_state = self._read_state()
        snapshot_id = committed_state["datasets"][0]["replicas"][0]["snapshot_id"]

        pair = sidecar.visibility.load_pair(self.private_path, self.public_path)
        policy = sidecar.load_policy(self.policy_path, pair)
        catalog = sidecar.materializer.load_catalog(self.catalog_path, pair)
        dataset = policy.datasets[0]
        checkout, registry_entry = sidecar._catalog_checkout(
            pair,
            catalog,
            self.portfolio,
            dataset,
        )
        capture = sidecar.capture_dataset(dataset, checkout, registry_entry)

        def restore_snapshot(
            _restic: Path,
            _ssh: Path,
            _known_hosts: Path,
            _target: sidecar.Target,
            _staged_target: sidecar.StagedTarget,
            _snapshot_id: str,
            restore_root: Path,
            *,
            cwd: Path,
        ) -> None:
            self.assertTrue(str(restore_root).startswith(str(cwd / "restores")))
            namespace = restore_root / sidecar.INTERNAL_NAMESPACE
            payload_root = namespace / "payload"
            payload_root.mkdir(mode=0o700, parents=True)
            manifest = namespace / "manifest.json"
            manifest.write_bytes(sidecar._portable_manifest_bytes(dataset, capture.files))
            manifest.chmod(0o400)
            for captured in capture.files:
                destination = payload_root / captured.relative_path
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                destination.write_bytes((checkout / captured.relative_path).read_bytes())
                destination.chmod(0o400)
            for directory, subdirectories, _files in os.walk(
                namespace,
                topdown=False,
            ):
                for subdirectory in subdirectories:
                    Path(directory, subdirectory).chmod(0o500)
                Path(directory).chmod(0o500)

        arguments = self._arguments("drill")
        evidence_path = Path(arguments[arguments.index("--evidence") + 1])
        with (
            mock.patch.object(sidecar, "_run_restic_check"),
            mock.patch.object(
                sidecar,
                "_run_restic_recorded_snapshot",
                return_value=snapshot_id,
            ),
            mock.patch.object(
                sidecar,
                "_run_restic_restore",
                side_effect=restore_snapshot,
            ) as restore,
        ):
            result, stdout, stderr = self._main(arguments)

        self.assertEqual(result, 0, stderr)
        self.assertIn(
            f"verified\t{self.DATASET_ID}\tverified=1/1\trequired=1",
            stdout,
        )
        self.assertIn("sidecar restore drill complete", stdout)
        restore.assert_called_once()
        self.assertEqual(tuple((self.control / "spool").iterdir()), ())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(evidence_path.stat().st_mode), 0o600)
        self.assertEqual(evidence_path.stat().st_nlink, 1)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["registry_id"], "sidecar-synthetic-registry")
        self.assertEqual(evidence["manifest_format"], "portable-files-v1")
        self.assertEqual(evidence["state_generation"], 1)
        self.assertEqual(
            evidence["state_sha256"],
            sidecar._canonical_document_hash(committed_state),
        )
        self.assertNotEqual(evidence["state_sha256"], initial_state_sha256)
        self.assertEqual(evidence["datasets"][0]["status"], "verified")
        self.assertEqual(
            evidence["datasets"][0]["replicas"],
            [
                {
                    "target_id": "target-1",
                    "snapshot_id": snapshot_id,
                    "status": "verified",
                }
            ],
        )
        serialized_evidence = json.dumps(evidence)
        self.assertNotIn(str(self.checkout), serialized_evidence)
        self.assertNotIn("sftp:", serialized_evidence)

        before = evidence_path.read_bytes()
        result, _stdout, stderr = self._main(arguments)
        self.assertEqual(result, 2)
        self.assertIn("refuses to overwrite", stderr)
        self.assertEqual(evidence_path.read_bytes(), before)

    def test_drill_rejects_a_recorded_snapshot_identity_mismatch(self) -> None:
        self._init_state()
        backup_result, _stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(backup_result, 0, stderr)
        with (
            mock.patch.object(sidecar, "_run_restic_check"),
            mock.patch.object(
                sidecar,
                "_run_restic_recorded_snapshot",
                side_effect=sidecar.SidecarError(
                    "restic recorded snapshot identifier does not match state"
                ),
            ),
            mock.patch.object(sidecar, "_run_restic_restore") as restore,
        ):
            result, stdout, stderr = self._main(self._arguments("drill"))

        self.assertEqual(result, 3)
        self.assertIn("not-verified", stdout)
        self.assertIn("restore drill was degraded", stderr)
        restore.assert_not_called()
        evidence = json.loads(
            (self.control / "drill-evidence.local.json").read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["datasets"][0]["status"], "not-verified")
        self.assertEqual(
            evidence["datasets"][0]["replicas"][0]["status"],
            "not-verified",
        )

    def test_mesh_drill_distinguishes_full_degraded_quorum_and_below_quorum(
        self,
    ) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=())
        self._init_state()
        backup_result, _stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(backup_result, 0, stderr)
        state = self._read_state()["datasets"][0]
        snapshots = {
            replica["target_id"]: replica["snapshot_id"]
            for replica in state["replicas"]
        }

        cases = (
            (3, 0, "verified"),
            (2, 3, "verified-degraded"),
            (1, 3, "not-verified"),
        )
        for successful_targets, expected_exit, expected_status in cases:
            with self.subTest(successful_targets=successful_targets):
                evidence_path = (
                    self.control
                    / f"drill-mesh-{successful_targets}.local.json"
                )
                arguments = self._arguments("drill")
                arguments[arguments.index("--evidence") + 1] = str(evidence_path)

                def check_target(
                    _restic: Path,
                    _ssh: Path,
                    _known_hosts: Path,
                    target: sidecar.Target,
                    _staged_target: sidecar.StagedTarget,
                    *,
                    cwd: Path,
                ) -> None:
                    self.assertTrue(cwd.is_dir())
                    target_number = int(target.target_id.rsplit("-", 1)[1])
                    if target_number > successful_targets:
                        raise sidecar.SidecarError("synthetic target outage")

                def recorded_snapshot(
                    _restic: Path,
                    _ssh: Path,
                    _known_hosts: Path,
                    target: sidecar.Target,
                    _staged_target: sidecar.StagedTarget,
                    _dataset: sidecar.DatasetPolicy,
                    snapshot_id: str,
                    *,
                    cwd: Path,
                ) -> str:
                    self.assertTrue(cwd.is_dir())
                    self.assertEqual(snapshot_id, snapshots[target.target_id])
                    return snapshot_id

                with (
                    mock.patch.object(
                        sidecar,
                        "_run_restic_check",
                        side_effect=check_target,
                    ) as check,
                    mock.patch.object(
                        sidecar,
                        "_run_restic_recorded_snapshot",
                        side_effect=recorded_snapshot,
                    ),
                    mock.patch.object(sidecar, "_run_restic_restore"),
                    mock.patch.object(sidecar, "_verify_restored_snapshot"),
                ):
                    result, stdout, stderr = self._main(arguments)

                self.assertEqual(result, expected_exit, stderr)
                self.assertIn(
                    f"{expected_status}\t{self.DATASET_ID}\t"
                    f"verified={successful_targets}/3\trequired=2",
                    stdout,
                )
                self.assertEqual(check.call_count, 3)
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                dataset_evidence = evidence["datasets"][0]
                self.assertEqual(dataset_evidence["status"], expected_status)
                self.assertEqual(
                    dataset_evidence["verified_replicas"],
                    successful_targets,
                )
                self.assertEqual(len(dataset_evidence["replicas"]), 3)
                self.assertEqual(
                    sum(
                        replica["status"] == "verified"
                        for replica in dataset_evidence["replicas"]
                    ),
                    successful_targets,
                )

    def test_known_hosts_swap_uses_staged_copy_and_prevents_state_commit(self) -> None:
        self._init_state()
        script = self.fake_restic.read_text(encoding="utf-8")
        insertion_point = (
            "repository = repository_file.read_text(encoding='utf-8').strip()\n"
        )
        mutation = (
            insertion_point
            + f"live_known_hosts = pathlib.Path({str(self.known_hosts)!r})\n"
            + "live_known_hosts.write_text('swapped host key\\n', encoding='utf-8')\n"
            + "live_known_hosts.chmod(0o600)\n"
        )
        self.assertIn(insertion_point, script)
        self.fake_restic.write_text(
            script.replace(insertion_point, mutation, 1),
            encoding="utf-8",
        )
        self.fake_restic.chmod(0o700)

        result, stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("known_hosts content changed", stderr)
        self.assertEqual(self._read_state()["state_generation"], 0)
        calls = self._read_restic_log()
        ssh_arguments = next(
            value.removeprefix("sftp.command=")
            for value in calls[0]["argv"]
            if value.startswith("sftp.command=")
        )
        self.assertNotIn(str(self.known_hosts.resolve()), ssh_arguments)
        self.assertIn("UserKnownHostsFile=", ssh_arguments)
        self.assertIn("/spool/run-", ssh_arguments)
        self.assertNotIn("synthetic known-host key", str(calls))
        self.assertEqual(list((self.control / "spool").iterdir()), [])

    def test_target_credentials_are_globally_unique_across_roles(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=())
        payload = json.loads(self.targets_path.read_text(encoding="utf-8"))
        targets = payload["target_sets"][0]["targets"]
        targets[1]["identity_file"] = targets[0]["password_file"]
        self._write_json(self.targets_path, payload)

        result, _stdout, stderr = self._main(self._arguments("init-state"))
        self.assertEqual(result, 2)
        self.assertIn("globally unique", stderr)

    def test_hosted_target_set_is_exactly_one_of_one(self) -> None:
        second_repository = self.secrets / "repository-2.txt"
        second_password = self.secrets / "password-2.txt"
        second_identity = self.secrets / "identity-2.txt"
        self._write_secret(
            second_repository,
            "sftp:backup@backup-2.example.invalid:/repo-2\n",
        )
        self._write_secret(second_password, "synthetic-password-2\n")
        self._write_secret(second_identity, "synthetic-identity-2\n")
        payload = json.loads(self.targets_path.read_text(encoding="utf-8"))
        payload["target_sets"][0]["targets"].append(
            {
                "target_id": "target-2",
                "repository_file": str(second_repository.resolve()),
                "password_file": str(second_password.resolve()),
                "identity_file": str(second_identity.resolve()),
                "mesh_address": None,
                "failure_domain": "failure-domain-2",
            }
        )
        self._write_json(self.targets_path, payload)

        result, _stdout, stderr = self._main(self._arguments("init-state"))
        self.assertEqual(result, 2)
        self.assertIn("exactly one target", stderr)

    def test_head_tracked_staged_deletion_is_not_treated_as_sidecar_data(self) -> None:
        self._git("add", "-f", "sidecar-data/alpha.txt")
        self._git("commit", "-qm", "track synthetic data")
        self._git("rm", "--cached", "-q", "sidecar-data/alpha.txt")
        with self.assertRaisesRegex(sidecar.SidecarError, "Git-tracked"):
            sidecar._prove_untracked_and_ignored(
                self.checkout,
                ["sidecar-data/alpha.txt"],
            )

    def test_public_or_loopback_addresses_are_not_mesh_targets(self) -> None:
        for unsafe_address in ("8.8.8.8", "127.0.0.1"):
            with self.subTest(unsafe_address=unsafe_address):
                self._write_policy(tier="mesh-only")
                self._write_targets(tier="mesh-only", failures=())
                payload = json.loads(self.targets_path.read_text(encoding="utf-8"))
                target = payload["target_sets"][0]["targets"][0]
                repository = Path(target["repository_file"])
                repository.write_text(
                    f"sftp:backup@{unsafe_address}:/repo-1\n",
                    encoding="utf-8",
                )
                repository.chmod(0o600)
                target["mesh_address"] = unsafe_address
                self._write_json(self.targets_path, payload)
                result, _stdout, stderr = self._main(self._arguments("init-state"))
                self.assertEqual(result, 2)
                self.assertIn("RFC1918 IPv4", stderr)

    def test_mesh_quorum_commits_partial_state_and_returns_nonzero(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=(3,))
        self._init_state()
        result, stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 3)
        self.assertIn("committed-degraded", stdout)
        self.assertNotIn("RAW-RESTIC", stderr)
        dataset = self._read_state()["datasets"][0]
        self.assertEqual(dataset["sequence"], 1)
        self.assertEqual(len(dataset["replicas"]), 2)

    def test_below_mesh_quorum_retains_prior_standalone_state(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=(2, 3))
        self._init_state()
        result, stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 3)
        self.assertIn("not-committed", stdout)
        self.assertNotIn("RAW-RESTIC", stderr)
        state = self._read_state()
        self.assertEqual(state["state_generation"], 0)
        self.assertEqual(state["datasets"][0]["sequence"], 0)

    def test_drill_uses_committed_ids_after_a_below_quorum_orphan(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=())
        self._init_state()
        result, _stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 0, stderr)
        committed_state = self._read_state()
        committed_replicas = {
            replica["target_id"]: replica["snapshot_id"]
            for replica in committed_state["datasets"][0]["replicas"]
        }
        committed_bytes = self.state_path.read_bytes()
        orphan_snapshot = "f" * 64
        self.assertNotIn(orphan_snapshot, committed_replicas.values())

        self.fake_restic.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "arguments = sys.argv[1:]\n"
            "repository_file = pathlib.Path(\n"
            "    arguments[arguments.index('--repository-file') + 1]\n"
            ")\n"
            "repository = repository_file.read_text(encoding='utf-8').strip()\n"
            "sys.stdin.buffer.read()\n"
            "if repository.endswith(('/repo-2', '/repo-3')):\n"
            "    raise SystemExit(17)\n"
            f"print(json.dumps({{'snapshot_id': {orphan_snapshot!r}}}))\n",
            encoding="utf-8",
        )
        self.fake_restic.chmod(0o700)

        result, stdout, stderr = self._main(self._arguments("backup"))
        self.assertEqual(result, 3, stderr)
        self.assertIn("not-committed", stdout)
        self.assertEqual(self.state_path.read_bytes(), committed_bytes)

        inspected: list[str] = []
        restored: list[str] = []

        def inspect_recorded(
            _restic: Path,
            _ssh: Path,
            _known_hosts: Path,
            target: sidecar.Target,
            _staged_target: sidecar.StagedTarget,
            _dataset: sidecar.DatasetPolicy,
            snapshot_id: str,
            *,
            cwd: Path,
        ) -> str:
            self.assertTrue(cwd.is_dir())
            self.assertEqual(snapshot_id, committed_replicas[target.target_id])
            inspected.append(snapshot_id)
            return snapshot_id

        def restore_recorded(
            _restic: Path,
            _ssh: Path,
            _known_hosts: Path,
            _target: sidecar.Target,
            _staged_target: sidecar.StagedTarget,
            snapshot_id: str,
            _restore_root: Path,
            *,
            cwd: Path,
        ) -> None:
            self.assertTrue(cwd.is_dir())
            restored.append(snapshot_id)

        with (
            mock.patch.object(sidecar, "_run_restic_check"),
            mock.patch.object(
                sidecar,
                "_run_restic_recorded_snapshot",
                side_effect=inspect_recorded,
            ),
            mock.patch.object(
                sidecar,
                "_run_restic_restore",
                side_effect=restore_recorded,
            ),
            mock.patch.object(sidecar, "_verify_restored_snapshot"),
        ):
            result, stdout, stderr = self._main(self._arguments("drill"))

        self.assertEqual(result, 0, stderr)
        self.assertIn("verified", stdout)
        self.assertCountEqual(inspected, committed_replicas.values())
        self.assertCountEqual(restored, committed_replicas.values())
        self.assertNotIn(orphan_snapshot, inspected)
        self.assertNotIn(orphan_snapshot, restored)

    def test_hosted_target_set_cannot_back_a_mesh_dataset(self) -> None:
        self._write_policy(
            tier="mesh-only",
            target_set_id="targets-hosted-encrypted",
        )
        result, _stdout, stderr = self._main(self._arguments("init-state"))
        self.assertEqual(result, 2)
        self.assertIn("tier does not match", stderr)

    def test_mesh_repository_host_must_match_literal_mesh_address(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=())
        payload = json.loads(self.targets_path.read_text(encoding="utf-8"))
        payload["target_sets"][0]["targets"][0]["mesh_address"] = "10.44.0.99"
        self._write_json(self.targets_path, payload)
        result, _stdout, stderr = self._main(self._arguments("init-state"))
        self.assertEqual(result, 2)
        self.assertIn("does not match", stderr)

    def test_info_exclude_is_not_accepted_as_a_tracked_ignore_rule(self) -> None:
        local_only = self.checkout / "local-only"
        local_only.mkdir()
        (local_only / "value.txt").write_text("local\n", encoding="utf-8")
        (self.checkout / ".git" / "info" / "exclude").write_text(
            "local-only/\n",
            encoding="utf-8",
        )
        self._write_policy(tier="hosted-encrypted", selector="local-only")
        self._init_state()
        result, _stdout, stderr = self._main(self._arguments("plan"))
        self.assertEqual(result, 2)
        self.assertIn("ignore rule", stderr)

    def test_symlinks_hardlinks_and_limits_fail_before_restic(self) -> None:
        cases = ("symlink", "hardlink", "limit")
        for case in cases:
            with self.subTest(case=case):
                if self.state_path.exists():
                    self.state_path.unlink()
                selected = self.checkout / "sidecar-data"
                link = selected / "unsafe-link"
                hard = selected / "unsafe-hard"
                if link.exists() or link.is_symlink():
                    link.unlink()
                if hard.exists():
                    hard.unlink()
                self._write_policy(tier="hosted-encrypted")
                if case == "symlink":
                    link.symlink_to(selected / "alpha.txt")
                elif case == "hardlink":
                    os.link(selected / "alpha.txt", hard)
                else:
                    self._write_policy(tier="hosted-encrypted", max_files=1)
                self._init_state()
                result, _stdout, _stderr = self._main(self._arguments("backup"))
                self.assertEqual(result, 2)
                self.assertFalse(Path(str(self.fake_restic) + ".log").exists())


if __name__ == "__main__":
    unittest.main()
