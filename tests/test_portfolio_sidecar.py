"""Focused regressions for the three-level portfolio sidecar runtime."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


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
        if command == "backup":
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

    def _read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _read_restic_log(self) -> list[dict[str, object]]:
        log_path = Path(str(self.fake_restic) + ".log")
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]

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
            b"sidecar-data/alpha.txt\0sidecar-data/nested/beta.bin\0",
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
