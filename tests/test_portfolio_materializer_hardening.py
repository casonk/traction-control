"""Security and concurrency regressions for portfolio materialization."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import portfolio_materializer as materializer  # noqa: E402
import repository_visibility as visibility  # noqa: E402


REAL_MATERIALIZER_RUN = materializer._run


class PortfolioMaterializerHardeningTests(unittest.TestCase):
    PRIVATE_ID = "R_private_synthetic"
    PUBLIC_ID = "R_public_synthetic"
    PRIVATE_SLUG = "synthetic-owner/private-synthetic"
    PUBLIC_SLUG = "synthetic-owner/public-synthetic"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.portfolio = self.root / "portfolio"
        self.registry = self.root / "registry"
        self.remotes = self.root / "remotes"
        self.seeds = self.root / "seeds"
        for directory in (
            self.portfolio,
            self.registry,
            self.remotes,
            self.seeds,
        ):
            directory.mkdir()
        self.private_path = self.registry / "private.local.json"
        self.public_path = self.registry / "public.local.json"
        self.catalog_path = self.registry / "portfolio.local.json"
        self._write_registry(
            generation=1,
            private_entries=((self.PRIVATE_ID, self.PRIVATE_SLUG),),
            public_entries=((self.PUBLIC_ID, self.PUBLIC_SLUG),),
        )
        self._write_catalog(
            (
                self._catalog_row(
                    self.PRIVATE_ID,
                    "vault/private-synthetic-checkout",
                ),
                self._catalog_row(
                    self.PUBLIC_ID,
                    "repos/public-synthetic",
                ),
            ),
            registry_generation=1,
            catalog_generation=1,
        )
        self._create_remote(self.PRIVATE_SLUG)
        self._create_remote(self.PUBLIC_SLUG)
        self.pair = self._load_pair()
        self.catalog = materializer.load_catalog(
            self.catalog_path,
            self.pair,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(
        self,
        *arguments: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        for key in tuple(environment):
            if key.startswith("GIT_CONFIG_KEY_") or key.startswith(
                "GIT_CONFIG_VALUE_"
            ):
                del environment[key]
        environment.pop("GIT_CONFIG_COUNT", None)
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            env=environment,
        )

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _write_registry(
        self,
        *,
        generation: int,
        private_entries: tuple[tuple[str, str], ...],
        public_entries: tuple[tuple[str, str], ...],
    ) -> None:
        common = {
            "schema_version": 1,
            "registry_id": "materializer-hardening-test",
            "generation": generation,
        }
        for path, entry_visibility, entries in (
            (self.private_path, "private", private_entries),
            (self.public_path, "public", public_entries),
        ):
            repositories = [
                {"id": repository_id, "slug": slug}
                for repository_id, slug in entries
            ]
            repositories.sort(
                key=lambda entry: (
                    str(entry["slug"]).casefold(),
                    str(entry["id"]),
                )
            )
            self._write_json(
                path,
                {
                    **common,
                    "visibility": entry_visibility,
                    "repositories": repositories,
                },
            )

    def _load_pair(self) -> visibility.RegistryPair:
        return visibility.load_pair(self.private_path, self.public_path)

    def _catalog_row(
        self,
        repository_id: str,
        relative_path: str,
        *,
        lifecycle: str = "active",
        sync_policy: str = "fetch-only",
        desired_presence: str = "checkout",
    ) -> dict[str, object]:
        return {
            "repository_id": repository_id,
            "relative_path": relative_path,
            "lifecycle": lifecycle,
            "sync_policy": sync_policy,
            "desired_presence": desired_presence,
        }

    def _write_catalog(
        self,
        rows: tuple[dict[str, object], ...] | list[dict[str, object]],
        *,
        registry_generation: int,
        catalog_generation: int,
        path: Path | None = None,
    ) -> None:
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                str(row["relative_path"]).casefold(),
                str(row["repository_id"]),
            ),
        )
        self._write_json(
            path or self.catalog_path,
            {
                "schema_version": 1,
                "registry_id": "materializer-hardening-test",
                "registry_generation": registry_generation,
                "catalog_generation": catalog_generation,
                "repositories": ordered_rows,
            },
        )

    def _create_remote(self, slug: str) -> Path:
        owner, name = slug.split("/", 1)
        seed = self.seeds / owner / name
        bare = self.remotes / owner / f"{name}.git"
        seed.parent.mkdir(parents=True, exist_ok=True)
        bare.parent.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "main", str(seed))
        self._git("-C", str(seed), "config", "user.name", "Synthetic Test")
        self._git(
            "-C",
            str(seed),
            "config",
            "user.email",
            "synthetic@example.invalid",
        )
        (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        self._git("-C", str(seed), "add", "README.md")
        self._git("-C", str(seed), "commit", "-qm", "initial")
        self._git("clone", "-q", "--bare", str(seed), str(bare))
        self._git("-C", str(seed), "remote", "add", "origin", str(bare))
        return bare

    def _push_seed_commit(self, slug: str, filename: str) -> str:
        owner, name = slug.split("/", 1)
        seed = self.seeds / owner / name
        (seed / filename).write_text(f"{filename}\n", encoding="utf-8")
        self._git("-C", str(seed), "add", filename)
        self._git("-C", str(seed), "commit", "-qm", f"add {filename}")
        self._git("-C", str(seed), "push", "-q", "origin", "main")
        return self._git("-C", str(seed), "rev-parse", "HEAD").stdout.strip()

    def _rewrite_environment(
        self,
        *,
        global_config: Path | None = None,
    ) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": (
                    f"url.file://{self.remotes.resolve().as_posix()}/.insteadOf"
                ),
                "GIT_CONFIG_VALUE_0": "https://github.com/",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        if global_config is not None:
            environment["GIT_CONFIG_GLOBAL"] = str(global_config)
        return environment

    def _materialize(
        self,
        *,
        pair: visibility.RegistryPair | None = None,
        catalog: materializer.CatalogDocument | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        product_environment = environment or self._rewrite_environment()

        def controlled_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._run_product_git(
                command,
                product_environment,
                timeout=timeout,
            )

        with mock.patch.object(
            materializer,
            "_run",
            side_effect=controlled_run,
        ):
            materializer.materialize(
                pair or self.pair,
                catalog or self.catalog,
                self.portfolio,
                clone_protocol="https",
                gh_command="unused",
                skip_github=True,
            )

    def _run_product_git(
        self,
        command: list[str] | tuple[str, ...],
        environment: dict[str, str],
        *,
        timeout: float = 300.0,
    ) -> subprocess.CompletedProcess[str]:
        call_environment = environment.copy()
        verb_index = 1
        if len(command) > 2 and command[0] == "git" and command[1] == "-C":
            verb_index = 3
        uses_local_transport_rewrite = (
            command
            and command[0] == "git"
            and len(command) > verb_index
            and command[verb_index] in {"clone", "fetch"}
        )
        if not uses_local_transport_rewrite:
            call_environment.pop("GIT_CONFIG_COUNT", None)
            for key in tuple(call_environment):
                if key.startswith("GIT_CONFIG_KEY_") or key.startswith(
                    "GIT_CONFIG_VALUE_"
                ):
                    del call_environment[key]
        with mock.patch.dict(os.environ, call_environment, clear=True):
            return REAL_MATERIALIZER_RUN(command, timeout=timeout)

    def _clone_checkout(
        self,
        slug: str,
        target: Path,
        *,
        depth: int | None = None,
    ) -> None:
        owner, name = slug.split("/", 1)
        remote = self.remotes / owner / f"{name}.git"
        target.parent.mkdir(parents=True, exist_ok=True)
        arguments = ["clone", "-q"]
        if depth is not None:
            arguments.extend(("--depth", str(depth)))
            source = f"file://{remote.resolve().as_posix()}"
        else:
            source = str(remote)
        arguments.extend((source, str(target)))
        self._git(*arguments)
        self._git(
            "-C",
            str(target),
            "remote",
            "set-url",
            "origin",
            f"https://github.com/{slug}.git",
        )

    def _init_no_remote_repository(self, relative_path: str) -> Path:
        repository = self.portfolio / relative_path
        repository.parent.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "main", str(repository))
        return repository

    def test_reconcile_adds_discovered_and_default_ids_without_policy_drift(
        self,
    ) -> None:
        discovered_id = "R_discovered_synthetic"
        discovered_slug = "synthetic-owner/discovered-synthetic"
        default_id = "R_default_synthetic"
        default_slug = "synthetic-owner/default-synthetic"
        self._create_remote(discovered_slug)
        self._create_remote(default_slug)
        discovered_path = self.portfolio / "custom" / "discovered-synthetic"
        self._clone_checkout(discovered_slug, discovered_path)
        self._init_no_remote_repository("local/no-remote-synthetic")

        old_rows = (
            self._catalog_row(
                self.PRIVATE_ID,
                "custom/preserved-private",
                lifecycle="archived",
                sync_policy="manual",
                desired_presence="absent",
            ),
            self._catalog_row(
                self.PUBLIC_ID,
                "custom/preserved-public",
                lifecycle="active",
                sync_policy="fetch-only",
                desired_presence="checkout",
            ),
        )
        self._write_catalog(
            old_rows,
            registry_generation=1,
            catalog_generation=7,
        )
        self._write_registry(
            generation=2,
            private_entries=(
                (discovered_id, discovered_slug),
                (self.PRIVATE_ID, self.PRIVATE_SLUG),
            ),
            public_entries=(
                (default_id, default_slug),
                (self.PUBLIC_ID, self.PUBLIC_SLUG),
            ),
        )
        grown_pair = self._load_pair()

        reconciled, added = materializer.reconcile_catalog(
            grown_pair,
            self.catalog_path,
            self.portfolio,
        )

        self.assertEqual(added, 2)
        self.assertEqual(reconciled.registry_generation, 2)
        self.assertEqual(reconciled.catalog_generation, 8)
        entries = {
            entry.repository_id: entry
            for entry in reconciled.repositories
        }
        self.assertEqual(
            entries[self.PRIVATE_ID],
            materializer.CatalogEntry(
                repository_id=self.PRIVATE_ID,
                relative_path="custom/preserved-private",
                lifecycle="archived",
                sync_policy="manual",
                desired_presence="absent",
            ),
        )
        self.assertEqual(
            entries[self.PUBLIC_ID],
            materializer.CatalogEntry(
                repository_id=self.PUBLIC_ID,
                relative_path="custom/preserved-public",
                lifecycle="active",
                sync_policy="fetch-only",
                desired_presence="checkout",
            ),
        )
        self.assertEqual(
            entries[discovered_id].relative_path,
            "custom/discovered-synthetic",
        )
        self.assertEqual(
            entries[default_id].relative_path,
            "github/synthetic-owner/default-synthetic",
        )
        self.assertEqual(entries[discovered_id].sync_policy, "fetch-only")
        self.assertEqual(entries[default_id].desired_presence, "checkout")

    def test_reconcile_rejects_an_id_removed_from_the_registry(self) -> None:
        self._write_registry(
            generation=2,
            private_entries=((self.PRIVATE_ID, self.PRIVATE_SLUG),),
            public_entries=(),
        )
        reduced_pair = self._load_pair()

        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "removed from the visibility registry",
        ):
            materializer.reconcile_catalog(
                reduced_pair,
                self.catalog_path,
                self.portfolio,
            )

    def test_materialize_and_sync_reject_stale_caller_snapshots(self) -> None:
        first_snapshot = materializer.load_catalog(
            self.catalog_path,
            self.pair,
        )
        rows = [
            self._catalog_row(
                entry.repository_id,
                entry.relative_path,
                lifecycle=entry.lifecycle,
                sync_policy=entry.sync_policy,
                desired_presence=entry.desired_presence,
            )
            for entry in first_snapshot.repositories
        ]
        self._write_catalog(
            rows,
            registry_generation=1,
            catalog_generation=2,
        )
        with mock.patch.object(materializer, "_run") as run:
            with self.assertRaisesRegex(
                materializer.MaterializerError,
                "changed before the locked operation",
            ):
                materializer.materialize(
                    self.pair,
                    first_snapshot,
                    self.portfolio,
                    clone_protocol="https",
                    gh_command="unused",
                    skip_github=True,
                )
            run.assert_not_called()

        second_snapshot = materializer.load_catalog(
            self.catalog_path,
            self.pair,
        )
        self._write_catalog(
            rows,
            registry_generation=1,
            catalog_generation=3,
        )
        with mock.patch.object(materializer, "_run") as run:
            with self.assertRaisesRegex(
                materializer.MaterializerError,
                "changed before the locked operation",
            ):
                materializer.synchronize(
                    self.pair,
                    second_snapshot,
                    self.portfolio,
                )
            run.assert_not_called()

    def test_mutating_operations_reject_stale_complete_registry_snapshots(
        self,
    ) -> None:
        def stale_snapshots(
            drift_kind: str,
        ) -> tuple[
            visibility.RegistryPair,
            materializer.CatalogDocument,
        ]:
            self._write_registry(
                generation=1,
                private_entries=((self.PRIVATE_ID, self.PRIVATE_SLUG),),
                public_entries=((self.PUBLIC_ID, self.PUBLIC_SLUG),),
            )
            stale_pair = self._load_pair()
            stale_catalog = materializer.load_catalog(
                self.catalog_path,
                stale_pair,
            )
            if drift_kind == "generation":
                self._write_registry(
                    generation=2,
                    private_entries=((self.PRIVATE_ID, self.PRIVATE_SLUG),),
                    public_entries=((self.PUBLIC_ID, self.PUBLIC_SLUG),),
                )
            else:
                # Preserve the generation and membership while changing the
                # authoritative visibility classification. Comparing only a
                # generation or ID set would miss this stale snapshot.
                self._write_registry(
                    generation=1,
                    private_entries=(
                        (self.PRIVATE_ID, self.PRIVATE_SLUG),
                        (self.PUBLIC_ID, self.PUBLIC_SLUG),
                    ),
                    public_entries=(),
                )
            return stale_pair, stale_catalog

        def run_operation(
            operation: str,
            stale_pair: visibility.RegistryPair,
            stale_catalog: materializer.CatalogDocument,
        ) -> None:
            if operation == "materialize":
                materializer.materialize(
                    stale_pair,
                    stale_catalog,
                    self.portfolio,
                    clone_protocol="https",
                    gh_command="unused",
                    skip_github=True,
                )
            elif operation == "sync":
                materializer.synchronize(
                    stale_pair,
                    stale_catalog,
                    self.portfolio,
                )
            elif operation == "refresh":
                materializer.refresh_archive_states(
                    stale_pair,
                    stale_catalog,
                    gh_command="unused",
                )
            else:
                materializer.reconcile_catalog(
                    stale_pair,
                    self.catalog_path,
                    self.portfolio,
                )

        for drift_kind in ("generation", "content"):
            for operation in ("materialize", "sync", "refresh", "reconcile"):
                with self.subTest(
                    drift_kind=drift_kind,
                    operation=operation,
                ):
                    stale_pair, stale_catalog = stale_snapshots(drift_kind)
                    with (
                        mock.patch.object(materializer, "_run") as command_run,
                        mock.patch.object(
                            materializer,
                            "_read_remote_entry_archive_state",
                        ) as archive_read,
                        mock.patch.object(
                            materializer,
                            "_discover_registered_mappings",
                        ) as discovery,
                    ):
                        with self.assertRaisesRegex(
                            materializer.MaterializerError,
                            "visibility registry content or generation changed",
                        ):
                            run_operation(
                                operation,
                                stale_pair,
                                stale_catalog,
                            )
                    command_run.assert_not_called()
                    archive_read.assert_not_called()
                    discovery.assert_not_called()

    def test_registry_drift_at_catalog_lock_boundary_fails_closed(self) -> None:
        real_catalog_lock = materializer._CatalogLock

        def run_operation(
            operation: str,
            pair: visibility.RegistryPair,
            catalog: materializer.CatalogDocument,
        ) -> None:
            if operation == "materialize":
                materializer.materialize(
                    pair,
                    catalog,
                    self.portfolio,
                    clone_protocol="https",
                    gh_command="unused",
                    skip_github=True,
                )
            elif operation == "sync":
                materializer.synchronize(pair, catalog, self.portfolio)
            elif operation == "refresh":
                materializer.refresh_archive_states(
                    pair,
                    catalog,
                    gh_command="unused",
                )
            else:
                materializer.reconcile_catalog(
                    pair,
                    self.catalog_path,
                    self.portfolio,
                )

        for drift_kind in ("generation", "content"):
            for operation in ("materialize", "sync", "refresh", "reconcile"):
                with self.subTest(
                    drift_kind=drift_kind,
                    operation=operation,
                ):
                    self._write_registry(
                        generation=1,
                        private_entries=(
                            (self.PRIVATE_ID, self.PRIVATE_SLUG),
                        ),
                        public_entries=(
                            (self.PUBLIC_ID, self.PUBLIC_SLUG),
                        ),
                    )
                    pair = self._load_pair()
                    catalog = materializer.load_catalog(
                        self.catalog_path,
                        pair,
                    )
                    test_case = self

                    class DriftOnEnter:
                        def __init__(self, catalog_path: Path) -> None:
                            self.delegate = real_catalog_lock(catalog_path)

                        def __enter__(self) -> object:
                            if drift_kind == "generation":
                                test_case._write_registry(
                                    generation=2,
                                    private_entries=((
                                        test_case.PRIVATE_ID,
                                        test_case.PRIVATE_SLUG,
                                    ),),
                                    public_entries=((
                                        test_case.PUBLIC_ID,
                                        test_case.PUBLIC_SLUG,
                                    ),),
                                )
                            else:
                                test_case._write_registry(
                                    generation=1,
                                    private_entries=(
                                        (
                                            test_case.PRIVATE_ID,
                                            test_case.PRIVATE_SLUG,
                                        ),
                                        (
                                            test_case.PUBLIC_ID,
                                            test_case.PUBLIC_SLUG,
                                        ),
                                    ),
                                    public_entries=(),
                                )
                            return self.delegate.__enter__()

                        def __exit__(
                            self,
                            exc_type: object,
                            exc: object,
                            traceback: object,
                        ) -> None:
                            self.delegate.__exit__(exc_type, exc, traceback)

                    with (
                        mock.patch.object(
                            materializer,
                            "_CatalogLock",
                            DriftOnEnter,
                        ),
                        mock.patch.object(materializer, "_run") as command_run,
                        mock.patch.object(
                            materializer,
                            "_read_remote_entry_archive_state",
                            return_value=False,
                        ),
                        mock.patch.object(
                            materializer,
                            "_discover_registered_mappings",
                        ) as discovery,
                    ):
                        with self.assertRaisesRegex(
                            materializer.MaterializerError,
                            "visibility registry content or generation changed",
                        ):
                            run_operation(operation, pair, catalog)
                    command_run.assert_not_called()
                    discovery.assert_not_called()

    def test_catalog_paths_reject_dot_control_non_nfc_and_git_components(
        self,
    ) -> None:
        invalid_paths = (
            "./private-synthetic",
            "repos/./private-synthetic",
            "repos/../private-synthetic",
            "repos/private-\nsynthetic",
            "repos/private-\u202esynthetic",
            "repos/Cafe\u0301-synthetic",
            "repos/.GiT/private-synthetic",
        )
        for index, invalid_path in enumerate(invalid_paths, start=2):
            with self.subTest(relative_path=repr(invalid_path)):
                rows = (
                    self._catalog_row(self.PRIVATE_ID, invalid_path),
                    self._catalog_row(
                        self.PUBLIC_ID,
                        "repos/public-synthetic",
                    ),
                )
                self._write_catalog(
                    rows,
                    registry_generation=1,
                    catalog_generation=index,
                )
                with self.assertRaises(materializer.MaterializerError):
                    materializer.load_catalog(self.catalog_path, self.pair)

    def test_init_ignores_no_remote_repository(self) -> None:
        no_remote = self._init_no_remote_repository(
            "local/no-remote-synthetic"
        )
        initialized_path = self.registry / "initialized.local.json"

        initialized = materializer.initialize_catalog(
            self.pair,
            initialized_path,
            self.portfolio,
        )

        self.assertTrue(no_remote.is_dir())
        self.assertEqual(
            {
                entry.repository_id: entry.relative_path
                for entry in initialized.repositories
            },
            {
                self.PRIVATE_ID: (
                    "github/synthetic-owner/private-synthetic"
                ),
                self.PUBLIC_ID: (
                    "github/synthetic-owner/public-synthetic"
                ),
            },
        )

    def test_init_rejects_registered_checkout_with_unclassified_backup(
        self,
    ) -> None:
        checkout = self.portfolio / "repos" / "registered-synthetic"
        self._clone_checkout(self.PRIVATE_SLUG, checkout)
        self._git(
            "-C",
            str(checkout),
            "remote",
            "add",
            "backup",
            str(self.remotes / "unclassified-backup.git"),
        )
        initialized_path = self.registry / "initialized.local.json"

        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "not a canonical GitHub repository",
        ):
            materializer.initialize_catalog(
                self.pair,
                initialized_path,
                self.portfolio,
            )
        self.assertFalse(initialized_path.exists())

    def test_reconcile_ignores_no_remote_but_rejects_mixed_backup(
        self,
    ) -> None:
        new_id = "R_growth_synthetic"
        new_slug = "synthetic-owner/growth-synthetic"
        self._create_remote(new_slug)
        self._init_no_remote_repository("local/no-remote-synthetic")
        mixed_checkout = self.portfolio / "discovered" / "growth-synthetic"
        self._clone_checkout(new_slug, mixed_checkout)
        self._git(
            "-C",
            str(mixed_checkout),
            "remote",
            "add",
            "backup",
            str(self.remotes / "unclassified-growth-backup.git"),
        )
        self._write_registry(
            generation=2,
            private_entries=(
                (self.PRIVATE_ID, self.PRIVATE_SLUG),
                (new_id, new_slug),
            ),
            public_entries=((self.PUBLIC_ID, self.PUBLIC_SLUG),),
        )
        grown_pair = self._load_pair()

        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "not a canonical GitHub repository",
        ):
            materializer.reconcile_catalog(
                grown_pair,
                self.catalog_path,
                self.portfolio,
            )
        stale = materializer.load_catalog(
            self.catalog_path,
            grown_pair,
            allow_stale_registry=True,
        )
        self.assertEqual(stale.registry_generation, 1)

    def test_plan_redacts_private_and_unmanaged_locations_by_default(
        self,
    ) -> None:
        unmanaged_relative = "local/private-unmanaged-synthetic"
        self._init_no_remote_repository(unmanaged_relative)

        redacted = "\n".join(
            materializer.plan_operations(
                self.pair,
                self.catalog,
                self.portfolio,
            )
        )
        detailed = "\n".join(
            materializer.plan_operations(
                self.pair,
                self.catalog,
                self.portfolio,
                show_slugs=True,
            )
        )

        self.assertIn(f"repository-id:{self.PRIVATE_ID}", redacted)
        self.assertNotIn(self.PRIVATE_SLUG, redacted)
        self.assertNotIn("vault/private-synthetic-checkout", redacted)
        self.assertNotIn(unmanaged_relative, redacted)
        self.assertRegex(redacted, r"review-unmanaged\tpath-sha256:[0-9a-f]{16}")
        self.assertIn(self.PRIVATE_SLUG, detailed)
        self.assertIn("vault/private-synthetic-checkout", detailed)
        self.assertIn(f"review-unmanaged\t{unmanaged_relative}", detailed)

    def test_archive_state_lookup_forces_the_public_github_host(self) -> None:
        fake_gh = self.root / "observing-fake-gh"
        invocation_log = self.root / "observing-fake-gh.log"
        payload = json.dumps(
            {
                "id": self.PRIVATE_ID,
                "nameWithOwner": self.PRIVATE_SLUG,
                "visibility": "PRIVATE",
                "isArchived": False,
            }
        )
        fake_gh.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"${{GH_HOST-}}\" > '{invocation_log}'\n"
            f"printf '%s\\n' \"$@\" >> '{invocation_log}'\n"
            f"printf '%s\\n' '{payload}'\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o700)
        entry = visibility.RepositoryEntry(
            self.PRIVATE_ID,
            self.PRIVATE_SLUG,
        )

        with mock.patch.dict(
            os.environ,
            {"GH_HOST": "hostile-enterprise.invalid"},
            clear=False,
        ):
            archived = materializer._read_remote_entry_archive_state(
                "private",
                entry,
                str(fake_gh),
            )

        self.assertFalse(archived)
        invocation = invocation_log.read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(invocation[0], "github.com")
        self.assertEqual(
            invocation[1:],
            [
                "repo",
                "view",
                self.PRIVATE_SLUG,
                "--json",
                "id,nameWithOwner,visibility,isArchived",
            ],
        )

    def test_materialize_cli_defaults_to_ssh_and_https_is_explicit(
        self,
    ) -> None:
        common_arguments = [
            "--private",
            str(self.private_path),
            "--public",
            str(self.public_path),
            "--catalog",
            str(self.catalog_path),
            "--portfolio-root",
            str(self.portfolio),
            "--skip-github",
        ]
        with mock.patch.object(materializer, "materialize") as operation:
            with redirect_stdout(StringIO()):
                result = materializer.main(
                    ["materialize", *common_arguments]
                )
        self.assertEqual(result, 0)
        self.assertEqual(
            operation.call_args.kwargs["clone_protocol"],
            "ssh",
        )

        with mock.patch.object(materializer, "materialize") as operation:
            with redirect_stdout(StringIO()):
                result = materializer.main(
                    [
                        "materialize",
                        *common_arguments,
                        "--clone-protocol",
                        "https",
                    ]
                )
        self.assertEqual(result, 0)
        self.assertEqual(
            operation.call_args.kwargs["clone_protocol"],
            "https",
        )

        for protocol, expected_url in (
            (
                "ssh",
                "git@github.com:synthetic-owner/public-synthetic.git",
            ),
            (
                "https",
                "https://github.com/synthetic-owner/public-synthetic.git",
            ),
        ):
            commands: list[tuple[str, ...]] = []

            def fail_clone(
                command: list[str] | tuple[str, ...],
                *,
                timeout: float = 300.0,
            ) -> subprocess.CompletedProcess[str]:
                commands.append(tuple(command))
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "synthetic transport stop",
                )

            with self.subTest(protocol=protocol):
                with mock.patch.object(
                    materializer,
                    "_run",
                    side_effect=fail_clone,
                ):
                    with self.assertRaisesRegex(
                        materializer.MaterializerError,
                        "Git clone failed",
                    ):
                        materializer.materialize(
                            self.pair,
                            self.catalog,
                            self.portfolio,
                            clone_protocol=protocol,
                            gh_command="unused",
                            skip_github=True,
                        )
                self.assertEqual(commands[0][0:2], ("git", "clone"))
                self.assertIn(expected_url, commands[0])

    def test_dirty_manual_checkout_is_skipped_during_sync(self) -> None:
        rows = (
            self._catalog_row(
                self.PRIVATE_ID,
                "vault/private-synthetic-checkout",
                sync_policy="manual",
            ),
            self._catalog_row(
                self.PUBLIC_ID,
                "repos/public-synthetic",
            ),
        )
        self._write_catalog(
            rows,
            registry_generation=1,
            catalog_generation=2,
        )
        catalog = materializer.load_catalog(self.catalog_path, self.pair)
        environment = self._rewrite_environment()
        self._materialize(catalog=catalog, environment=environment)
        private_checkout = (
            self.portfolio / "vault" / "private-synthetic-checkout"
        )
        dirty_file = private_checkout / "manual-local-change.txt"
        dirty_file.write_text("preserve\n", encoding="utf-8")
        commands: list[tuple[str, ...]] = []
        def recording_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            commands.append(tuple(command))
            return self._run_product_git(
                command,
                environment,
                timeout=timeout,
            )

        with mock.patch.object(
            materializer,
            "_run",
            side_effect=recording_run,
        ):
            materializer.synchronize(self.pair, catalog, self.portfolio)

        fetch_targets = [
            command[2]
            for command in commands
            if len(command) > 3
            and command[0] == "git"
            and command[1] == "-C"
            and command[3] == "fetch"
        ]
        self.assertEqual(
            fetch_targets,
            [
                str(
                    (
                        self.portfolio
                        / "repos"
                        / "public-synthetic"
                    ).resolve()
                )
            ],
        )
        self.assertEqual(dirty_file.read_text(encoding="utf-8"), "preserve\n")

    def test_shallow_checkout_is_rejected(self) -> None:
        checkout = self.portfolio / "cases" / "shallow-synthetic"
        self._clone_checkout(self.PRIVATE_SLUG, checkout, depth=1)
        expected = visibility.RepositoryEntry(
            self.PRIVATE_ID,
            self.PRIVATE_SLUG,
        )

        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "shallow checkout is not allowed",
        ):
            materializer.verify_checkout(
                checkout,
                expected,
                require_clean=False,
            )

    def test_sparse_checkout_is_rejected(self) -> None:
        checkout = self.portfolio / "cases" / "sparse-synthetic"
        self._clone_checkout(self.PRIVATE_SLUG, checkout)
        self._git(
            "-C",
            str(checkout),
            "config",
            "core.sparseCheckout",
            "true",
        )
        expected = visibility.RepositoryEntry(
            self.PRIVATE_ID,
            self.PRIVATE_SLUG,
        )

        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "sparse checkout is not allowed",
        ):
            materializer.verify_checkout(
                checkout,
                expected,
                require_clean=False,
            )

    def test_partial_checkout_is_rejected(self) -> None:
        checkout = self.portfolio / "cases" / "partial-synthetic"
        self._clone_checkout(self.PRIVATE_SLUG, checkout)
        self._git(
            "-C",
            str(checkout),
            "config",
            "remote.origin.promisor",
            "true",
        )
        expected = visibility.RepositoryEntry(
            self.PRIVATE_ID,
            self.PRIVATE_SLUG,
        )

        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "partial clone is not allowed",
        ):
            materializer.verify_checkout(
                checkout,
                expected,
                require_clean=False,
            )

    def test_clone_and_fetch_do_not_execute_ambient_git_extensions(self) -> None:
        child_slug = "synthetic-owner/submodule-child-synthetic"
        child_remote = self._create_remote(child_slug)
        private_seed = (
            self.seeds
            / "synthetic-owner"
            / "private-synthetic"
        )
        self._git(
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(private_seed),
            "submodule",
            "add",
            "-q",
            str(child_remote),
            "vendor/submodule-child-synthetic",
        )
        self._git(
            "-C",
            str(private_seed),
            "config",
            "-f",
            ".gitmodules",
            "submodule.vendor/submodule-child-synthetic.url",
            f"https://github.com/{child_slug}.git",
        )
        (private_seed / ".gitattributes").write_text(
            "payload.txt filter=sentinel-filter\n",
            encoding="utf-8",
        )
        (private_seed / "payload.txt").write_text(
            "synthetic payload\n",
            encoding="utf-8",
        )
        self._git(
            "-C",
            str(private_seed),
            "add",
            ".gitattributes",
            ".gitmodules",
            "payload.txt",
            "vendor/submodule-child-synthetic",
        )
        self._git(
            "-C",
            str(private_seed),
            "commit",
            "-qm",
            "add inert extension fixtures",
        )
        self._git(
            "-C",
            str(private_seed),
            "push",
            "-q",
            "origin",
            "main",
        )

        sentinels = self.root / "sentinels"
        hooks = self.root / "ambient-hooks"
        scripts = self.root / "ambient-scripts"
        for directory in (sentinels, hooks, scripts):
            directory.mkdir()
        hook_marker = sentinels / "hook-ran"
        filter_marker = sentinels / "filter-ran"
        fsmonitor_marker = sentinels / "fsmonitor-ran"
        submodule_marker = sentinels / "submodule-ran"
        hook = hooks / "post-checkout"
        filter_script = scripts / "filter-smudge"
        fsmonitor_script = scripts / "fsmonitor"
        submodule_script = scripts / "submodule-update"
        hook.write_text(
            f"#!/bin/sh\nprintf ran > {hook_marker}\n",
            encoding="utf-8",
        )
        filter_script.write_text(
            f"#!/bin/sh\nprintf ran > {filter_marker}\ncat\n",
            encoding="utf-8",
        )
        fsmonitor_script.write_text(
            f"#!/bin/sh\nprintf ran > {fsmonitor_marker}\nprintf '\\0'\n",
            encoding="utf-8",
        )
        submodule_script.write_text(
            f"#!/bin/sh\nprintf ran > {submodule_marker}\n",
            encoding="utf-8",
        )
        for script in (
            hook,
            filter_script,
            fsmonitor_script,
            submodule_script,
        ):
            script.chmod(0o700)

        ambient_config = self.root / "ambient.gitconfig"
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "core.hooksPath",
            str(hooks),
        )
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "core.fsmonitor",
            str(fsmonitor_script),
        )
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "filter.sentinel-filter.smudge",
            str(filter_script),
        )
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "filter.sentinel-filter.required",
            "true",
        )
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "submodule.recurse",
            "true",
        )
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "fetch.recurseSubmodules",
            "true",
        )
        self._git(
            "config",
            "--file",
            str(ambient_config),
            "submodule.vendor/submodule-child-synthetic.update",
            f"!{submodule_script}",
        )
        environment = self._rewrite_environment(
            global_config=ambient_config,
        )

        self._materialize(environment=environment)
        private_checkout = (
            self.portfolio / "vault" / "private-synthetic-checkout"
        )
        self.assertEqual(
            (private_checkout / "payload.txt").read_text(encoding="utf-8"),
            "synthetic payload\n",
        )
        self.assertFalse(
            (
                private_checkout
                / "vendor"
                / "submodule-child-synthetic"
                / ".git"
            ).exists()
        )
        self._push_seed_commit(self.PRIVATE_SLUG, "fetch-synthetic.txt")
        def controlled_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._run_product_git(
                command,
                environment,
                timeout=timeout,
            )

        with mock.patch.object(
            materializer,
            "_run",
            side_effect=controlled_run,
        ):
            materializer.synchronize(
                self.pair,
                self.catalog,
                self.portfolio,
            )

        self.assertEqual(tuple(sentinels.iterdir()), ())


if __name__ == "__main__":
    unittest.main()
