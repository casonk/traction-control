"""Adversarial regressions for the ignored-data portfolio sidecar."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "scripts"
TESTS = REPOSITORY_ROOT / "tests"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import portfolio_materializer as materializer  # noqa: E402
import portfolio_sidecar as sidecar  # noqa: E402
import repository_visibility as visibility  # noqa: E402
import test_portfolio_sidecar as sidecar_fixture  # noqa: E402


class PortfolioSidecarHardeningTests(unittest.TestCase):
    """Exercise fail-closed behavior with synthetic repositories and secrets."""

    def setUp(self) -> None:
        self.fixture = sidecar_fixture.PortfolioSidecarTests(methodName="runTest")
        self.fixture.setUp()
        self._staging_roots: list[Path] = []

    def tearDown(self) -> None:
        for root in self._staging_roots:
            for directory, subdirectories, _files in os.walk(root, topdown=False):
                for subdirectory in subdirectories:
                    Path(directory, subdirectory).chmod(0o700)
                Path(directory).chmod(0o700)
        self.fixture.tearDown()

    def __getattr__(self, name: str) -> object:
        fixture = self.__dict__.get("fixture")
        if fixture is None:
            raise AttributeError(name)
        return getattr(fixture, name)

    def _policy_payload(self) -> dict[str, object]:
        return json.loads(self.policy_path.read_text(encoding="utf-8"))

    def _targets_payload(self) -> dict[str, object]:
        return json.loads(self.targets_path.read_text(encoding="utf-8"))

    def _loaded_capture_components(
        self,
    ) -> tuple[
        sidecar.DatasetPolicy,
        visibility.RepositoryEntry,
        Path,
    ]:
        pair = visibility.load_pair(self.private_path, self.public_path)
        policy = sidecar.load_policy(self.policy_path, pair)
        catalog = materializer.load_catalog(self.catalog_path, pair)
        dataset = policy.datasets[0]
        checkout, registry_entry = sidecar._catalog_checkout(
            pair,
            catalog,
            self.portfolio,
            dataset,
        )
        return dataset, registry_entry, checkout

    def _capture_into_staging(
        self,
        name: str,
    ) -> tuple[sidecar.DatasetCapture, sidecar.Target, sidecar.StagedTarget]:
        pair = visibility.load_pair(self.private_path, self.public_path)
        targets = sidecar.load_targets(self.targets_path, pair)
        target = targets.target_sets[0].targets[0]
        dataset, registry_entry, checkout = self._loaded_capture_components()
        staging_root = self.fixture.root / name
        staging_root.mkdir(mode=0o700)
        staging_root.chmod(0o700)
        self._staging_roots.append(staging_root)
        capture = sidecar.capture_dataset(
            dataset,
            checkout,
            registry_entry,
            staging_root=staging_root,
        )
        staged_target = sidecar.StagedTarget(
            repository_file=target.repository_file,
            password_file=target.password_file,
            identity_file=target.identity_file,
        )
        return capture, target, staged_target

    def _assert_init_rejected(self, message: str | None = None) -> str:
        result, _stdout, stderr = self._main(self._arguments("init-state"))
        self.assertEqual(result, 2, stderr)
        self.assertFalse(self.state_path.exists())
        if message is not None:
            self.assertIn(message, stderr)
        return stderr

    def _assert_process_group_gone(self, process_group: int) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.killpg(process_group, 0)
            except (ProcessLookupError, PermissionError):
                return
            if time.monotonic() >= deadline:
                try:
                    os.killpg(process_group, sidecar.signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.fail("restic process group survived bounded-runner cleanup")
            time.sleep(0.02)

    def test_selector_validation_rejects_unsafe_nfc_and_git_paths(self) -> None:
        unsafe_selectors = (
            "/absolute/private-data",
            "../private-data",
            "private-data/../escape",
            "private-data//value",
            "private-data/.git/config",
            "private-data\\value",
            "private-data/*",
            "private-e\N{COMBINING ACUTE ACCENT}",
        )
        for selector in unsafe_selectors:
            with self.subTest(selector=repr(selector)):
                self._write_policy(tier="hosted-encrypted", selector=selector)
                self._assert_init_rejected()

    def test_selector_case_collisions_and_parent_child_overlap_are_rejected(
        self,
    ) -> None:
        for selectors in (("Data", "data"), ("Data", "data/child")):
            with self.subTest(selectors=selectors):
                self._write_policy(tier="hosted-encrypted")
                payload = self._policy_payload()
                payload["datasets"][0]["selectors"] = list(selectors)
                self._write_json(self.policy_path, payload)
                self._assert_init_rejected("selectors")

    def test_casefold_alias_of_tracked_path_is_rejected(self) -> None:
        tracked = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"Private/secret.txt\0",
            stderr=b"",
        )

        with (
            mock.patch.object(sidecar, "_run_git", side_effect=(tracked, tracked)),
            self.assertRaisesRegex(sidecar.SidecarError, "Git-tracked file"),
        ):
            sidecar._prove_untracked_and_ignored(
                Path("/synthetic/checkout"),
                ("private/secret.txt",),
            )

    def test_growing_input_copy_is_bounded_to_the_observed_size(self) -> None:
        source_path = self.fixture.root / "growing-input"
        destination_path = self.fixture.root / "bounded-copy"
        source_path.write_bytes(b"abcdefgh")
        source = os.open(source_path, os.O_RDONLY)
        destination = os.open(
            destination_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with self.assertRaisesRegex(sidecar.SidecarError, "grew"):
                sidecar._copy_descriptor(
                    source,
                    destination,
                    hashlib.sha256(),
                    expected_bytes=4,
                )
        finally:
            os.close(destination)
            os.close(source)

        self.assertEqual(destination_path.read_bytes(), b"abcd")

    def test_growing_hash_only_capture_is_bounded(self) -> None:
        dataset, registry_entry, checkout = self._loaded_capture_components()
        selected_file = checkout / "sidecar-data" / "alpha.txt"
        original_copy = sidecar._copy_descriptor
        grew = False

        def grow_before_hash(
            source: int,
            destination: int | None,
            digest: object,
            *,
            expected_bytes: int,
        ) -> None:
            nonlocal grew
            if destination is None and not grew:
                with selected_file.open("ab") as handle:
                    handle.write(b"growth")
                grew = True
            original_copy(
                source,
                destination,
                digest,
                expected_bytes=expected_bytes,
            )

        with (
            mock.patch.object(sidecar, "_copy_descriptor", side_effect=grow_before_hash),
            self.assertRaisesRegex(sidecar.SidecarError, "grew"),
        ):
            sidecar.capture_dataset(dataset, checkout, registry_entry)

    def test_growing_known_hosts_hash_is_bounded(self) -> None:
        original_copy = sidecar._copy_descriptor
        grew = False

        def grow_before_hash(
            source: int,
            destination: int | None,
            digest: object,
            *,
            expected_bytes: int,
        ) -> None:
            nonlocal grew
            if destination is None and not grew:
                with self.known_hosts.open("ab") as handle:
                    handle.write(b"growth")
                grew = True
            original_copy(
                source,
                destination,
                digest,
                expected_bytes=expected_bytes,
            )

        with (
            mock.patch.object(sidecar, "_copy_descriptor", side_effect=grow_before_hash),
            self.assertRaisesRegex(sidecar.SidecarError, "grew"),
        ):
            sidecar._stable_input_hash(
                self.known_hosts,
                forbidden_mode=0o022,
                label="known_hosts",
            )

    def test_directory_enumeration_reserves_budget_before_sorting(self) -> None:
        checkout = self.fixture.root / "bounded-enumeration"
        selected = checkout / "selected"
        selected.mkdir(parents=True)
        (selected / "one").write_text("one\n", encoding="utf-8")
        (selected / "two").write_text("two\n", encoding="utf-8")

        with self.assertRaisesRegex(sidecar.SidecarError, "traversal safety limit"):
            sidecar._enumerate_selector(
                checkout,
                "selected",
                checkout.lstat().st_dev,
                node_budget=[2],
            )

    def test_fifo_is_rejected_promptly_without_invoking_restic(self) -> None:
        fifo = self.checkout / "sidecar-data" / "unsafe-fifo"
        os.mkfifo(fifo, 0o600)
        self._init_state()

        started = time.monotonic()
        result, _stdout, stderr = self._main(self._arguments("backup"))
        elapsed = time.monotonic() - started

        self.assertEqual(result, 2, stderr)
        self.assertLess(elapsed, 2.0)
        self.assertIn("special file", stderr)
        self.assertFalse(Path(str(self.fake_restic) + ".log").exists())

    def test_regular_file_swap_to_fifo_cannot_block_open(self) -> None:
        dataset, registry_entry, checkout = self._loaded_capture_components()
        selected_file = checkout / "sidecar-data" / "alpha.txt"
        original_open = sidecar.os.open
        swapped = False

        def swap_before_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal swapped
            if path == "alpha.txt" and dir_fd is not None and not swapped:
                self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))
                selected_file.unlink()
                os.mkfifo(selected_file, 0o600)
                swapped = True
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with (
            mock.patch.object(sidecar.os, "open", side_effect=swap_before_open),
            self.assertRaisesRegex(sidecar.SidecarError, "changed before capture"),
        ):
            sidecar.capture_dataset(dataset, checkout, registry_entry)

        self.assertTrue(swapped)

    def test_hardlinked_selected_file_is_rejected_before_restic(self) -> None:
        source = self.checkout / "sidecar-data" / "alpha.txt"
        os.link(source, self.checkout / "sidecar-data" / "second-link")
        self._init_state()

        result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("hard-linked", stderr)
        self.assertFalse(Path(str(self.fake_restic) + ".log").exists())

    def test_unignored_selected_file_is_rejected(self) -> None:
        unignored = self.checkout / "not-ignored"
        unignored.mkdir()
        (unignored / "secret.txt").write_text("synthetic secret\n", encoding="utf-8")
        self._write_policy(tier="hosted-encrypted", selector="not-ignored")
        self._init_state()

        result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("untracked and ignored", stderr)

    def test_tracked_descendant_under_ignored_selector_is_rejected(self) -> None:
        tracked = self.checkout / "sidecar-data" / "tracked.txt"
        tracked.write_text("tracked synthetic data\n", encoding="utf-8")
        self._git("add", "-f", "sidecar-data/tracked.txt")
        self._git("commit", "-qm", "track descendant")
        self._init_state()

        result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("Git-tracked", stderr)
        self.assertFalse(Path(str(self.fake_restic) + ".log").exists())

    def test_staged_deleted_head_tracked_file_is_still_rejected(self) -> None:
        tracked = self.checkout / "sidecar-data" / "head-tracked.txt"
        tracked.write_text("HEAD-tracked private data\n", encoding="utf-8")
        self._git("add", "-f", "sidecar-data/head-tracked.txt")
        self._git("commit", "-qm", "track selected file")
        self._git("rm", "--cached", "sidecar-data/head-tracked.txt")
        self._init_state()

        result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("tracked changes", stderr)
        self.assertFalse(Path(str(self.fake_restic) + ".log").exists())

    def test_info_exclude_cannot_authorize_backup(self) -> None:
        selected = self.checkout / "info-only"
        selected.mkdir()
        (selected / "secret.txt").write_text("synthetic secret\n", encoding="utf-8")
        (self.checkout / ".git" / "info" / "exclude").write_text(
            "info-only/\n",
            encoding="utf-8",
        )
        self._write_policy(tier="hosted-encrypted", selector="info-only")
        self._init_state()

        result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("ignore rule", stderr)

    def test_global_exclude_cannot_authorize_backup(self) -> None:
        selected = self.checkout / "global-only"
        selected.mkdir()
        (selected / "secret.txt").write_text("synthetic secret\n", encoding="utf-8")
        excludes = self.fixture.root / "global-excludes"
        excludes.write_text("global-only/\n", encoding="utf-8")
        malicious_config = self.fixture.root / "global-gitconfig"
        malicious_config.write_text(
            f"[core]\n\texcludesFile = {excludes}\n",
            encoding="utf-8",
        )
        self._write_policy(tier="hosted-encrypted", selector="global-only")
        self._init_state()

        with mock.patch.dict(
            os.environ,
            {"GIT_CONFIG_GLOBAL": str(malicious_config)},
            clear=False,
        ):
            result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("untracked and ignored", stderr)

    def _assert_capture_drift_rejected(self, mutation: str) -> None:
        self._init_state()
        original_capture = sidecar._capture_regular_file
        mutated = False

        def capture_then_mutate(
            checkout_descriptor: int,
            relative_path: str,
            root_device: int,
            **kwargs: object,
        ) -> sidecar.FileSnapshot:
            nonlocal mutated
            captured = original_capture(
                checkout_descriptor,
                relative_path,
                root_device,
                **kwargs,
            )
            if not mutated:
                mutated = True
                if mutation == "ignore":
                    with self.checkout.joinpath(".gitignore").open(
                        "a",
                        encoding="utf-8",
                    ) as handle:
                        handle.write("# concurrent ignore drift\n")
                elif mutation == "index":
                    self._git("add", "-f", "sidecar-data/alpha.txt")
                else:
                    self._git(
                        "remote",
                        "set-url",
                        "origin",
                        "https://github.com/synthetic-owner/changed-during-capture.git",
                    )
            return captured

        with mock.patch.object(
            sidecar,
            "_capture_regular_file",
            side_effect=capture_then_mutate,
        ):
            result, _stdout, stderr = self._main(self._arguments("plan"))

        self.assertTrue(mutated)
        self.assertEqual(result, 2, stderr)

    def test_ignore_rule_drift_during_capture_is_rejected(self) -> None:
        self._assert_capture_drift_rejected("ignore")

    def test_index_drift_during_capture_is_rejected(self) -> None:
        self._assert_capture_drift_rejected("index")

    def test_repository_config_drift_during_capture_is_rejected(self) -> None:
        self._assert_capture_drift_rejected("config")

    def test_parent_symlink_substitution_cannot_send_outside_bytes(self) -> None:
        self._init_state()
        outside = self.fixture.root / "outside-private-tree"
        (outside / "nested").mkdir(parents=True)
        outside_canary = b"OUTSIDE-SIDECAR-CANARY"
        (outside / "alpha.txt").write_bytes(outside_canary)
        (outside / "nested" / "beta.bin").write_bytes(outside_canary)
        observed = self.fixture.root / "observed-restic-bytes"
        self.fake_restic.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, json, pathlib, sys\n"
            "paths = [p for p in sys.stdin.buffer.read().split(b'\\0') if p]\n"
            "content = b''.join(pathlib.Path(p.decode()).read_bytes() for p in paths)\n"
            f"pathlib.Path({str(observed)!r}).write_bytes(content)\n"
            "print(json.dumps({'snapshot_id': hashlib.sha256(content).hexdigest()}))\n",
            encoding="utf-8",
        )
        self.fake_restic.chmod(0o700)
        selected = self.checkout / "sidecar-data"
        parked = self.checkout / "sidecar-data-original"
        original_runner = sidecar._run_restic_backup

        def swap_parent_for_restic(
            restic: Path,
            ssh: Path,
            known_hosts: Path,
            target: sidecar.Target,
            staged_target: sidecar.StagedTarget,
            capture: sidecar.DatasetCapture,
        ) -> str:
            selected.rename(parked)
            selected.symlink_to(outside, target_is_directory=True)
            try:
                return original_runner(
                    restic,
                    ssh,
                    known_hosts,
                    target,
                    staged_target,
                    capture,
                )
            finally:
                selected.unlink()
                parked.rename(selected)

        with mock.patch.object(
            sidecar,
            "_run_restic_backup",
            side_effect=swap_parent_for_restic,
        ):
            result, _stdout, stderr = self._main(self._arguments("backup"))

        sent = observed.read_bytes() if observed.exists() else b""
        self.assertNotIn(outside_canary, sent)
        self.assertEqual(result, 0, stderr)
        state = self._read_state()
        self.assertEqual(state["state_generation"], 1)
        self.assertEqual(state["datasets"][0]["sequence"], 1)

    def test_source_credential_swap_cannot_redirect_staged_repository(self) -> None:
        self._init_state()
        redirect_canary = "sftp:backup@203.0.113.200:/redirected"
        observed = self.fixture.root / "observed-restic-repository"
        self.fake_restic.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, json, pathlib, sys\n"
            "arguments = sys.argv[1:]\n"
            "raw_files = sys.stdin.buffer.read()\n"
            "repository_file = pathlib.Path(arguments[arguments.index('--repository-file') + 1])\n"
            "repository = repository_file.read_text(encoding='utf-8').strip()\n"
            f"pathlib.Path({str(observed)!r}).write_text(repository, encoding='utf-8')\n"
            "snapshot = hashlib.sha256(repository.encode() + raw_files).hexdigest()\n"
            "print(json.dumps({'snapshot_id': snapshot}))\n",
            encoding="utf-8",
        )
        self.fake_restic.chmod(0o700)
        repository_file = self.secrets / "repository-1.txt"
        parked = self.secrets / "repository-1.original"
        original_runner = sidecar._run_restic_backup

        def swap_source_credential(
            restic: Path,
            ssh: Path,
            known_hosts: Path,
            target: sidecar.Target,
            staged_target: sidecar.StagedTarget,
            capture: sidecar.DatasetCapture,
        ) -> str:
            repository_file.rename(parked)
            self._write_secret(repository_file, f"{redirect_canary}\n")
            try:
                return original_runner(
                    restic,
                    ssh,
                    known_hosts,
                    target,
                    staged_target,
                    capture,
                )
            finally:
                repository_file.unlink()
                parked.rename(repository_file)

        with mock.patch.object(
            sidecar,
            "_run_restic_backup",
            side_effect=swap_source_credential,
        ):
            result, _stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 0, stderr)
        self.assertTrue(observed.exists())
        self.assertNotEqual(observed.read_text(encoding="utf-8"), redirect_canary)
        state = self._read_state()
        self.assertEqual(state["state_generation"], 1)
        self.assertEqual(state["datasets"][0]["sequence"], 1)

    def test_mesh_target_topology_requires_three_distinct_majority_replicas(
        self,
    ) -> None:
        cases = ("too-few", "non-majority", "duplicate-domain", "duplicate-address")
        for case in cases:
            with self.subTest(case=case):
                self._write_policy(tier="mesh-only")
                self._write_targets(tier="mesh-only", failures=())
                payload = self._targets_payload()
                target_set = payload["target_sets"][0]
                if case == "too-few":
                    target_set["targets"] = target_set["targets"][:2]
                elif case == "non-majority":
                    target_set["required_acks"] = 1
                elif case == "duplicate-domain":
                    target_set["targets"][1]["failure_domain"] = target_set[
                        "targets"
                    ][0]["failure_domain"]
                else:
                    target_set["targets"][1]["mesh_address"] = "10.44.0.1"
                    repository_path = Path(
                        target_set["targets"][1]["repository_file"]
                    )
                    self._write_secret(
                        repository_path,
                        "sftp:backup@10.44.0.1:/repo-2\n",
                    )
                self._write_json(self.targets_path, payload)
                self._assert_init_rejected()

    def test_mesh_targets_reject_non_private_and_non_unicast_addresses(self) -> None:
        invalid_addresses = (
            "8.8.8.8",
            "127.0.0.1",
            "169.254.10.20",
            "224.0.0.1",
            "0.0.0.0",
        )
        for address in invalid_addresses:
            with self.subTest(address=address):
                self._write_policy(tier="mesh-only")
                self._write_targets(tier="mesh-only", failures=())
                payload = self._targets_payload()
                first = payload["target_sets"][0]["targets"][0]
                first["mesh_address"] = address
                repository_file = Path(first["repository_file"])
                self._write_secret(
                    repository_file,
                    f"sftp:backup@{address}:/repo-1\n",
                )
                self._write_json(self.targets_path, payload)
                self._assert_init_rejected("RFC1918 IPv4")

    def test_schema_v1_rejects_ipv6_sftp_urls(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=())
        payload = self._targets_payload()
        first = payload["target_sets"][0]["targets"][0]
        first["mesh_address"] = "fd00::1"
        repository_file = Path(first["repository_file"])
        self._write_secret(
            repository_file,
            "sftp://backup@[fd00::1]//repo-1\n",
        )
        self._write_json(self.targets_path, payload)

        self._assert_init_rejected("only scp-style")

    def test_sftp_repository_requires_an_explicit_user(self) -> None:
        repository_file = self.secrets / "repository-1.txt"
        self._write_secret(
            repository_file,
            "sftp:backup-1.example.invalid:/repo-1\n",
        )

        self._assert_init_rejected("explicit SSH user")

    def test_mesh_dataset_cannot_reference_a_hosted_target_set(self) -> None:
        self._write_policy(
            tier="mesh-only",
            target_set_id="targets-hosted-encrypted",
        )
        self._assert_init_rejected("tier does not match")

    def _assert_index_hidden_gitignore_change_is_rejected(self, flag: str) -> None:
        selected = self.checkout / "index-hidden"
        selected.mkdir()
        (selected / "secret.txt").write_text(
            "synthetic private data\n",
            encoding="utf-8",
        )
        with self.checkout.joinpath(".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("index-hidden/\n")
        self._git("update-index", flag, ".gitignore")
        self._write_policy(tier="hosted-encrypted", selector="index-hidden")
        self._init_state()

        result, _stdout, stderr = self._main(self._arguments("plan"))

        self.assertEqual(result, 2, stderr)
        self.assertIn("skip-worktree or assume-unchanged", stderr)

    def test_assume_unchanged_cannot_hide_modified_gitignore(self) -> None:
        self._assert_index_hidden_gitignore_change_is_rejected(
            "--assume-unchanged"
        )

    def test_skip_worktree_cannot_hide_modified_gitignore(self) -> None:
        self._assert_index_hidden_gitignore_change_is_rejected("--skip-worktree")

    def test_insecure_and_symlink_control_files_are_rejected(self) -> None:
        cases = ("world-readable", "symlink", "insecure-parent")
        for case in cases:
            with self.subTest(case=case):
                if case == "world-readable":
                    self.policy_path.chmod(0o644)
                elif case == "symlink":
                    target = self.fixture.root / "policy-target.json"
                    target.write_bytes(self.policy_path.read_bytes())
                    target.chmod(0o600)
                    self.policy_path.unlink()
                    self.policy_path.symlink_to(target)
                else:
                    self.control.chmod(0o755)
                self._assert_init_rejected()
                if case == "world-readable":
                    self.policy_path.chmod(0o600)
                elif case == "symlink":
                    self.policy_path.unlink()
                    self._write_policy(tier="hosted-encrypted")
                else:
                    self.control.chmod(0o700)

    def test_hardlinked_control_file_cannot_alias_a_tracked_path(self) -> None:
        tracked_alias = self.checkout / "tracked-policy-alias.json"
        os.link(self.policy_path, tracked_alias)
        self._git("add", tracked_alias.name)
        self._git("commit", "-qm", "add synthetic hard-link alias")

        self._assert_init_rejected("must not be hard-linked")

    def test_insecure_symlink_and_parent_exposed_credentials_are_rejected(self) -> None:
        repository_file = self.secrets / "repository-1.txt"
        cases = ("world-readable", "symlink", "insecure-parent")
        for case in cases:
            with self.subTest(case=case):
                if case == "world-readable":
                    repository_file.chmod(0o644)
                elif case == "symlink":
                    target = self.fixture.root / "repository-target.txt"
                    target.write_bytes(repository_file.read_bytes())
                    target.chmod(0o600)
                    repository_file.unlink()
                    repository_file.symlink_to(target)
                else:
                    self.secrets.chmod(0o755)
                self._assert_init_rejected()
                if case == "world-readable":
                    repository_file.chmod(0o600)
                elif case == "symlink":
                    repository_file.unlink()
                    self._write_secret(
                        repository_file,
                        "sftp:backup@backup-1.example.invalid:/repo-1\n",
                    )
                else:
                    self.secrets.chmod(0o700)

    def test_known_hosts_must_not_be_symlink_or_group_writable(self) -> None:
        self._init_state()
        for case in ("group-writable", "symlink"):
            with self.subTest(case=case):
                if case == "group-writable":
                    self.known_hosts.chmod(0o620)
                else:
                    target = self.fixture.root / "known-hosts-target"
                    target.write_text("synthetic key\n", encoding="utf-8")
                    target.chmod(0o600)
                    self.known_hosts.unlink()
                    self.known_hosts.symlink_to(target)
                result, _stdout, stderr = self._main(self._arguments("backup"))
                self.assertEqual(result, 2, stderr)
                if case == "group-writable":
                    self.known_hosts.chmod(0o600)
                else:
                    self.known_hosts.unlink()
                    self._write_secret(self.known_hosts, "synthetic known-host key\n")

    def test_secret_values_and_private_paths_are_redacted_from_errors(self) -> None:
        password_file = self.secrets / "password-1.txt"
        secret_canary = "SYNTHETIC-SECRET-CANARY"
        self._write_secret(password_file, f"{secret_canary}\nsecond-line\n")

        stderr = self._assert_init_rejected()

        self.assertNotIn(secret_canary, stderr)
        self.assertNotIn(str(password_file), stderr)
        self.assertNotIn(self.REPOSITORY_SLUG, stderr)

    def test_missing_private_input_paths_are_redacted_from_errors(self) -> None:
        missing_credential = (self.secrets / "missing-private-canary.txt").resolve()
        payload = self._targets_payload()
        payload["target_sets"][0]["targets"][0]["password_file"] = str(
            missing_credential
        )
        self._write_json(self.targets_path, payload)

        stderr = self._assert_init_rejected()

        self.assertNotIn(str(missing_credential), stderr)
        self.assertIn("unavailable or insecure", stderr)

    def test_private_registry_path_is_redacted_from_loader_errors(self) -> None:
        self.private_path.chmod(0o640)

        stderr = self._assert_init_rejected()

        self.assertNotIn(str(self.private_path), stderr)
        self.assertIn("registry, catalog, or portfolio-root", stderr)

    def test_fake_restic_receives_exact_argv_and_nul_stdin_contract(self) -> None:
        capture, target, staged_target = self._capture_into_staging(
            "restic-contract-staging"
        )
        snapshot_id = "a" * 64
        restic_output = (json.dumps({"snapshot_id": snapshot_id}) + "\n").encode()

        with (
            mock.patch.object(
                sidecar,
                "_run_bounded_process",
                return_value=(0, restic_output, b""),
            ) as runner,
            mock.patch.dict(
                os.environ,
                {
                    "HOME": "/ambient/home/must-not-leak",
                    "SSH_AUTH_SOCK": "/ambient/agent.sock",
                    "RESTIC_PASSWORD": "SHOULD-NOT-INHERIT",
                    "RESTIC_REPOSITORY": "SHOULD-NOT-INHERIT",
                },
                clear=False,
            ),
        ):
            observed_snapshot = sidecar._run_restic_backup(
                self.fake_restic,
                self.fake_ssh,
                self.known_hosts,
                target,
                staged_target,
                capture,
            )

        self.assertEqual(observed_snapshot, snapshot_id)
        command = runner.call_args.args[0]
        keyword = runner.call_args.kwargs
        self.assertEqual(command[0], str(self.fake_restic))
        self.assertEqual(
            command[-5:],
            ["backup", "--json", "--no-scan", "--files-from-raw", "-"],
        )
        self.assertNotIn("sidecar-data/alpha.txt", command)
        self.assertEqual(keyword["cwd"], capture.backup_root)
        self.assertEqual(keyword["timeout"], 3600.0)
        self.assertEqual(
            keyword["input_data"],
            b"sidecar-data/alpha.txt\0sidecar-data/nested/beta.bin\0",
        )
        self.assertFalse(any(value.startswith("sftp.args=") for value in command))
        ssh_arguments = next(
            value.removeprefix("sftp.command=")
            for value in command
            if value.startswith("sftp.command=")
        )
        self.assertEqual(ssh_arguments.split()[-2:], ["-s", "sftp"])
        self.assertIn("IdentitiesOnly=yes", ssh_arguments)
        self.assertIn("IdentityAgent=none", ssh_arguments)
        self.assertIn(
            f"IdentityFile={staged_target.identity_file}",
            ssh_arguments,
        )
        self.assertIn("PasswordAuthentication=no", ssh_arguments)
        self.assertNotIn("HOME", keyword["environment"])
        self.assertNotIn("SSH_AUTH_SOCK", keyword["environment"])
        self.assertNotIn("RESTIC_PASSWORD", keyword["environment"])
        self.assertNotIn("RESTIC_REPOSITORY", keyword["environment"])
        self.assertNotIn("shell", keyword)

    def test_restic_stdout_and_stderr_are_capped_during_execution(self) -> None:
        for stream_name, descriptor in (("stdout", 1), ("stderr", 2)):
            with self.subTest(stream=stream_name):
                producer = (
                    "import os\n"
                    "chunk = b'x' * 65536\n"
                    f"descriptor = {descriptor}\n"
                    "while True:\n"
                    "    os.write(descriptor, chunk)\n"
                )
                started = time.monotonic()
                with mock.patch.object(
                    sidecar,
                    "_kill_process_group",
                    wraps=sidecar._kill_process_group,
                ) as kill_process_group:
                    with self.assertRaisesRegex(
                        sidecar.SidecarError,
                        "output exceeded",
                    ):
                        sidecar._run_bounded_process(
                            [sys.executable, "-c", producer],
                            cwd=self.fixture.root,
                            input_data=b"",
                            environment={"PATH": os.environ.get("PATH", "")},
                            timeout=10.0,
                        )

                self.assertLess(time.monotonic() - started, 5.0)
                kill_process_group.assert_called_once()

    def test_restic_timeout_kills_the_isolated_process_group(self) -> None:
        started = time.monotonic()
        with mock.patch.object(
            sidecar,
            "_kill_process_group",
            wraps=sidecar._kill_process_group,
        ) as kill_process_group:
            with self.assertRaisesRegex(sidecar.SidecarError, "timed out"):
                sidecar._run_bounded_process(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=self.fixture.root,
                    input_data=b"",
                    environment={"PATH": os.environ.get("PATH", "")},
                    timeout=0.2,
                )

        self.assertLess(time.monotonic() - started, 3.0)
        kill_process_group.assert_called_once()

    def test_clean_process_completion_preserves_status_and_output(self) -> None:
        returncode, stdout, stderr = sidecar._run_bounded_process(
            [sys.executable, "-c", "print('synthetic-success')"],
            cwd=self.fixture.root,
            input_data=b"",
            environment={"PATH": os.environ.get("PATH", "")},
            timeout=10.0,
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, b"synthetic-success\n")
        self.assertEqual(stderr, b"")

    def test_bounded_runner_kills_descendants_after_leader_exit(self) -> None:
        group_file = self.fixture.root / "restic-process-group"
        launcher = (
            "import os, pathlib, signal, time\n"
            f"pathlib.Path({str(group_file)!r}).write_text(str(os.getpid()))\n"
            "signal.signal(signal.SIGPIPE, signal.SIG_IGN)\n"
            "child = os.fork()\n"
            "if child:\n"
            "    os._exit(0)\n"
            "chunk = b'x' * 65536\n"
            "try:\n"
            "    while True:\n"
            "        os.write(2, chunk)\n"
            "except BrokenPipeError:\n"
            "    time.sleep(60)\n"
        )
        observed_leader_exits: list[bool] = []
        original_kill = sidecar._kill_process_group

        def record_and_kill(process: subprocess.Popen[bytes]) -> None:
            observed_leader_exits.append(
                sidecar._process_exited_without_reaping(process)
            )
            original_kill(process)

        with mock.patch.object(
            sidecar,
            "_kill_process_group",
            side_effect=record_and_kill,
        ):
            with self.assertRaisesRegex(sidecar.SidecarError, "output exceeded"):
                sidecar._run_bounded_process(
                    [sys.executable, "-c", launcher],
                    cwd=self.fixture.root,
                    input_data=b"",
                    environment={"PATH": os.environ.get("PATH", "")},
                    timeout=10.0,
                )

        process_group = int(group_file.read_text(encoding="utf-8"))
        self.assertEqual(observed_leader_exits, [True])
        self._assert_process_group_gone(process_group)

    def test_selector_setup_failures_always_kill_the_process_group(self) -> None:
        for exception in (
            OSError(24, "synthetic descriptor exhaustion"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(exception).__name__):
                group_file = self.fixture.root / (
                    f"selector-failure-{type(exception).__name__}"
                )
                launcher = (
                    "import os, pathlib, time\n"
                    f"pathlib.Path({str(group_file)!r}).write_text(str(os.getpid()))\n"
                    "time.sleep(60)\n"
                )

                def fail_selector() -> None:
                    deadline = time.monotonic() + 2.0
                    while not group_file.exists():
                        if time.monotonic() >= deadline:
                            self.fail("synthetic restic process did not start")
                        time.sleep(0.01)
                    raise exception

                with mock.patch.object(
                    sidecar.selectors,
                    "DefaultSelector",
                    side_effect=fail_selector,
                ):
                    if isinstance(exception, OSError):
                        with self.assertRaisesRegex(
                            sidecar.SidecarError,
                            "could not complete",
                        ):
                            sidecar._run_bounded_process(
                                [sys.executable, "-c", launcher],
                                cwd=self.fixture.root,
                                input_data=b"",
                                environment={"PATH": os.environ.get("PATH", "")},
                                timeout=10.0,
                            )
                    else:
                        with self.assertRaises(KeyboardInterrupt):
                            sidecar._run_bounded_process(
                                [sys.executable, "-c", launcher],
                                cwd=self.fixture.root,
                                input_data=b"",
                                environment={"PATH": os.environ.get("PATH", "")},
                                timeout=10.0,
                            )

                self._assert_process_group_gone(
                    int(group_file.read_text(encoding="utf-8"))
                )

    def test_early_failure_reaps_an_instant_exit_after_killpg_eperm(self) -> None:
        original_popen = sidecar.subprocess.Popen
        spawned: list[subprocess.Popen[bytes]] = []

        def capture_popen(*arguments: object, **keywords: object) -> subprocess.Popen[bytes]:
            process = original_popen(*arguments, **keywords)
            spawned.append(process)
            return process

        def fail_selector() -> None:
            time.sleep(0.2)
            raise OSError(24, "synthetic descriptor exhaustion")

        with mock.patch.object(
            sidecar.subprocess,
            "Popen",
            side_effect=capture_popen,
        ), mock.patch.object(
            sidecar.selectors,
            "DefaultSelector",
            side_effect=fail_selector,
        ), mock.patch.object(
            sidecar.os,
            "killpg",
            side_effect=(PermissionError(), ProcessLookupError()),
        ) as killpg:
            with self.assertRaisesRegex(
                sidecar.SidecarError,
                "could not complete",
            ):
                sidecar._run_bounded_process(
                    [sys.executable, "-c", "pass"],
                    cwd=self.fixture.root,
                    input_data=b"",
                    environment={"PATH": os.environ.get("PATH", "")},
                    timeout=10.0,
                )

        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0].returncode, 0)
        self.assertEqual(killpg.call_count, 2)
        self.assertEqual(killpg.call_args_list[1].args[1], 0)

    def test_process_cleanup_failure_is_the_visible_terminal_error(self) -> None:
        original_kill = sidecar._kill_process_group

        def kill_then_report_failure(process: subprocess.Popen[bytes]) -> None:
            try:
                original_kill(process)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            raise sidecar.SidecarError("restic process cleanup failed")

        with mock.patch.object(
            sidecar,
            "_communicate_bounded_process",
            side_effect=sidecar.SidecarError("restic backup process timed out"),
        ), mock.patch.object(
            sidecar,
            "_kill_process_group",
            side_effect=kill_then_report_failure,
        ):
            with self.assertRaisesRegex(
                sidecar.SidecarError,
                "process cleanup failed",
            ) as raised:
                sidecar._run_bounded_process(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=self.fixture.root,
                    input_data=b"",
                    environment={"PATH": os.environ.get("PATH", "")},
                    timeout=10.0,
                )

        self.assertIsInstance(raised.exception.__cause__, sidecar.SidecarError)
        self.assertIn("timed out", str(raised.exception.__cause__))

    def test_reaped_leader_never_causes_a_blind_process_group_signal(self) -> None:
        process = mock.Mock(spec=subprocess.Popen)
        process.pid = 424242
        process.returncode = 0
        with mock.patch.object(
            sidecar.sys,
            "platform",
            "linux",
        ), mock.patch.object(
            sidecar,
            "_linux_group_has_owned_live_child",
            return_value=False,
        ) as has_owned_child, mock.patch.object(
            sidecar.os,
            "killpg",
        ) as killpg:
            sidecar._kill_process_group(process)

        has_owned_child.assert_called_once_with(process.pid)
        killpg.assert_not_called()
        process.wait.assert_not_called()

    def test_nondefault_sigchld_is_rejected_before_process_spawn(self) -> None:
        with mock.patch.object(
            sidecar.signal,
            "getsignal",
            return_value=sidecar.signal.SIG_IGN,
        ), mock.patch.object(sidecar.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                sidecar.SidecarError,
                "default SIGCHLD",
            ):
                sidecar._run_bounded_process(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=self.fixture.root,
                    input_data=b"",
                    environment={"PATH": os.environ.get("PATH", "")},
                    timeout=10.0,
                )

        popen.assert_not_called()

    def test_lost_process_ownership_never_causes_a_group_signal(self) -> None:
        process = mock.Mock(spec=subprocess.Popen)
        process.pid = 424243
        process.returncode = None
        with mock.patch.object(
            sidecar.os,
            "waitid",
            side_effect=ChildProcessError(),
            create=True,
        ):
            with self.assertRaisesRegex(
                sidecar.SidecarError,
                "ownership was lost",
            ):
                sidecar._process_exited_without_reaping(process)

        with mock.patch.object(sidecar.os, "killpg") as killpg:
            with self.assertRaisesRegex(
                sidecar.SidecarError,
                "was not signaled",
            ):
                sidecar._kill_process_group(process)

        killpg.assert_not_called()
        process.wait.assert_not_called()

    def test_below_quorum_never_advances_committed_state(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=(2, 3))
        self._init_state()
        before = self.state_path.read_bytes()

        result, stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 3, stderr)
        self.assertIn("not-committed", stdout)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_concurrent_backups_serialize_before_restic_and_update_state_safely(
        self,
    ) -> None:
        self._init_state()
        started = self.fixture.root / "concurrent-restic-started"
        release = self.fixture.root / "release-concurrent-restic"
        invocations = self.fixture.root / "concurrent-restic-invocations"
        self.fake_restic.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, json, pathlib, sys, time\n"
            f"started = pathlib.Path({str(started)!r})\n"
            f"release = pathlib.Path({str(release)!r})\n"
            f"invocations = pathlib.Path({str(invocations)!r})\n"
            "with invocations.open('a', encoding='utf-8') as handle:\n"
            "    handle.write('invoked\\n')\n"
            "started.touch()\n"
            "deadline = time.monotonic() + 10\n"
            "while not release.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.02)\n"
            "if not release.exists():\n"
            "    raise SystemExit(19)\n"
            "raw_files = sys.stdin.buffer.read()\n"
            "print(json.dumps({'snapshot_id': hashlib.sha256(raw_files).hexdigest()}))\n",
            encoding="utf-8",
        )
        self.fake_restic.chmod(0o700)
        command = [
            sys.executable,
            str(SCRIPTS / "portfolio_sidecar.py"),
            *self._arguments("backup"),
        ]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        first: subprocess.Popen[str] | None = None
        second: subprocess.Popen[str] | None = None
        try:
            first = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            deadline = time.monotonic() + 5.0
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(started.exists(), "first backup never reached fake Restic")

            second = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            observation_deadline = time.monotonic() + 0.75
            while time.monotonic() < observation_deadline:
                lines = (
                    invocations.read_text(encoding="utf-8").splitlines()
                    if invocations.exists()
                    else []
                )
                if len(lines) > 1 or second.poll() is not None:
                    break
                time.sleep(0.02)

            lines = invocations.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                lines,
                ["invoked"],
                "a concurrent command reached Restic before the first released its lock",
            )
            early_second_status = second.poll()
            if early_second_status is not None:
                self.assertEqual(early_second_status, 2)
            release.touch()
            first_stdout, first_stderr = first.communicate(timeout=15.0)
            second_stdout, second_stderr = second.communicate(timeout=15.0)
        finally:
            release.touch(exist_ok=True)
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()

        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertIn(second.returncode, (0, 2), second_stderr)
        state = self._read_state()
        expected_generation = 2 if second.returncode == 0 else 1
        self.assertEqual(state["state_generation"], expected_generation)
        self.assertEqual(state["datasets"][0]["sequence"], expected_generation)
        self.assertNotIn("synthetic-password", first_stdout + first_stderr)
        self.assertNotIn("synthetic-password", second_stdout + second_stderr)

    def test_quorum_with_partial_replicas_commits_but_returns_degraded(self) -> None:
        self._write_policy(tier="mesh-only")
        self._write_targets(tier="mesh-only", failures=(3,))
        self._init_state()

        result, stdout, stderr = self._main(self._arguments("backup"))

        self.assertEqual(result, 3)
        self.assertIn("committed-degraded", stdout)
        self.assertIn("partial or degraded", stderr)
        state = self._read_state()
        self.assertEqual(state["state_generation"], 1)
        self.assertEqual(state["datasets"][0]["sequence"], 1)
        self.assertEqual(len(state["datasets"][0]["replicas"]), 2)

    def test_state_replace_failure_preserves_previous_bytes_and_removes_temp(
        self,
    ) -> None:
        self._init_state()
        before = self.state_path.read_bytes()
        payload = self._read_state()
        payload["state_generation"] = 1

        with mock.patch.object(sidecar.os, "replace", side_effect=OSError("crash")):
            with self.assertRaises(sidecar.SidecarError):
                sidecar._write_state_json(self.state_path, payload, replace=True)

        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertFalse(
            tuple(self.control.glob(".sidecar-state.local.json.*.tmp.local.json"))
        )

    def test_repository_content_and_git_extensions_are_never_executed(self) -> None:
        marker = self.fixture.root / "repo-command-executed"
        command = self.fixture.root / "hostile-command.sh"
        command.write_text(
            "#!/bin/sh\n"
            f"printf invoked > {marker}\n"
            "cat\n",
            encoding="utf-8",
        )
        command.chmod(0o700)
        hooks = self.fixture.root / "hostile-hooks"
        hooks.mkdir()
        hook = hooks / "post-index-change"
        hook.write_text(command.read_text(encoding="utf-8"), encoding="utf-8")
        hook.chmod(0o700)
        attributes = self.checkout / ".gitattributes"
        attributes.write_text("sidecar-data/** filter=hostile\n", encoding="utf-8")
        self._git("add", ".gitattributes")
        self._git("commit", "-qm", "add hostile attributes fixture")
        self._git("config", "core.fsmonitor", str(command))
        self._git("config", "core.hooksPath", str(hooks))
        self._git("config", "filter.hostile.clean", str(command))
        selected_command = self.checkout / "sidecar-data" / "payload-command.sh"
        selected_command.write_text(command.read_text(encoding="utf-8"), encoding="utf-8")
        selected_command.chmod(0o700)
        self._init_state()
        hostile_environment = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": str(command),
            "GIT_CONFIG_KEY_1": "filter.hostile.clean",
            "GIT_CONFIG_VALUE_1": str(command),
        }

        with mock.patch.dict(os.environ, hostile_environment, clear=False):
            result, _stdout, stderr = self._main(self._arguments("plan"))

        self.assertEqual(result, 0, stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
