"""Offline tests for the paired repository visibility registry."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import repository_visibility as visibility  # noqa: E402


class RepositoryVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.private_path = self.root / "private.local.json"
        self.public_path = self.root / "public.local.json"

    def _payload(
        self,
        visibility_name: str,
        repositories: list[dict[str, str]] | None = None,
        *,
        generation: int = 7,
        registry_id: str = "test-portfolio",
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "registry_id": registry_id,
            "generation": generation,
            "visibility": visibility_name,
            "repositories": repositories or [],
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def _write_pair(
        self,
        private: list[dict[str, str]] | None = None,
        public: list[dict[str, str]] | None = None,
        *,
        private_generation: int = 7,
        public_generation: int = 7,
        private_registry_id: str = "test-portfolio",
        public_registry_id: str = "test-portfolio",
    ) -> None:
        self._write_json(
            self.private_path,
            self._payload(
                "private",
                private,
                generation=private_generation,
                registry_id=private_registry_id,
            ),
        )
        self._write_json(
            self.public_path,
            self._payload(
                "public",
                public,
                generation=public_generation,
                registry_id=public_registry_id,
            ),
        )

    def test_init_creates_a_secure_generation_zero_pair(self) -> None:
        visibility.initialize_pair(
            str(self.private_path),
            str(self.public_path),
            "test-portfolio",
        )

        pair = visibility.load_pair(self.private_path, self.public_path)
        self.assertEqual(pair.registry_id, "test-portfolio")
        self.assertEqual(pair.generation, 0)
        self.assertEqual(pair.entries, ())
        self.assertEqual(stat.S_IMODE(self.private_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.public_path.stat().st_mode), 0o600)

    def test_init_refuses_to_overwrite_either_half(self) -> None:
        self._write_json(self.private_path, {})
        with self.assertRaisesRegex(visibility.RegistryError, "refuses to overwrite"):
            visibility.initialize_pair(
                str(self.private_path),
                str(self.public_path),
                "test-portfolio",
            )

    def test_valid_pair_classifies_case_insensitively(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "example-owner/private-agent"}],
            public=[{"id": "R_public", "slug": "casonk/traction-control"}],
        )

        pair = visibility.load_pair(self.private_path, self.public_path)

        self.assertEqual(pair.classification("EXAMPLE-OWNER/PRIVATE-AGENT"), "private")
        self.assertEqual(pair.classification("casonk/traction-control"), "public")
        self.assertEqual(pair.classification("casonk/unmanaged"), "unclassified")

    def test_missing_file_is_rejected(self) -> None:
        self._write_json(self.private_path, self._payload("private"))
        with self.assertRaisesRegex(visibility.RegistryError, "not found"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_symlink_is_rejected(self) -> None:
        target = self.root / "target.json"
        self._write_json(target, self._payload("private"))
        self.private_path.symlink_to(target)
        self._write_json(self.public_path, self._payload("public"))

        with self.assertRaisesRegex(visibility.RegistryError, "non-symlink"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_group_or_other_permissions_are_rejected(self) -> None:
        self._write_pair()
        self.private_path.chmod(0o640)
        with self.assertRaisesRegex(visibility.RegistryError, "group or other"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_active_registry_inside_git_must_be_ignored_and_untracked(self) -> None:
        worktree = self.root / "worktree"
        worktree.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(worktree)],
            check=True,
            capture_output=True,
        )
        self.private_path = worktree / "private.local.json"
        self.public_path = worktree / "public.local.json"
        self._write_pair()

        with self.assertRaisesRegex(visibility.RegistryError, "must be ignored"):
            visibility.load_pair(self.private_path, self.public_path)

        (worktree / ".gitignore").write_text("*.local.json\n", encoding="utf-8")
        visibility.load_pair(self.private_path, self.public_path)

        subprocess.run(
            ["git", "-C", str(worktree), "add", "-f", "private.local.json"],
            check=True,
            capture_output=True,
        )
        with self.assertRaisesRegex(visibility.RegistryError, "must not be tracked"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_registry_temporary_files_remain_ignored_after_a_crash(self) -> None:
        worktree = self.root / "worktree"
        worktree.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(worktree)],
            check=True,
            capture_output=True,
        )
        (worktree / ".gitignore").write_text("*.local.json\n", encoding="utf-8")
        path = worktree / "private.local.json"
        temporary = visibility._write_temp(path, self._payload("private"))
        self.addCleanup(temporary.unlink, missing_ok=True)

        ignored = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                temporary.name,
            ],
            check=False,
            capture_output=True,
        )

        self.assertEqual(ignored.returncode, 0)
        self.assertTrue(temporary.name.endswith(".local.json"))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        self.private_path.write_text(
            '{"schema_version":1,"schema_version":1,'
            '"registry_id":"test-portfolio","generation":7,'
            '"visibility":"private","repositories":[]}\n',
            encoding="utf-8",
        )
        self.private_path.chmod(0o600)
        self._write_json(self.public_path, self._payload("public"))

        with self.assertRaisesRegex(visibility.RegistryError, "duplicate JSON key"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_unknown_or_missing_keys_are_rejected(self) -> None:
        private = self._payload("private")
        private["unexpected"] = True
        self._write_json(self.private_path, private)
        self._write_json(self.public_path, self._payload("public"))
        with self.assertRaisesRegex(visibility.RegistryError, "unknown unexpected"):
            visibility.load_pair(self.private_path, self.public_path)

        del private["unexpected"]
        del private["generation"]
        self._write_json(self.private_path, private)
        with self.assertRaisesRegex(visibility.RegistryError, "missing generation"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_boolean_schema_and_generation_values_are_rejected(self) -> None:
        private = self._payload("private")
        private["schema_version"] = True
        self._write_json(self.private_path, private)
        self._write_json(self.public_path, self._payload("public"))
        with self.assertRaisesRegex(visibility.RegistryError, "schema_version"):
            visibility.load_pair(self.private_path, self.public_path)

        private = self._payload("private")
        private["generation"] = True
        self._write_json(self.private_path, private)
        with self.assertRaisesRegex(visibility.RegistryError, "generation"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_pair_identity_and_generation_must_match(self) -> None:
        self._write_pair(public_generation=8)
        with self.assertRaisesRegex(visibility.RegistryError, "generations do not match"):
            visibility.load_pair(self.private_path, self.public_path)

        self._write_pair(public_registry_id="different-portfolio")
        with self.assertRaisesRegex(visibility.RegistryError, "registry_id values do not match"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_per_file_visibility_must_match_its_role(self) -> None:
        self._write_json(self.private_path, self._payload("public"))
        self._write_json(self.public_path, self._payload("public"))
        with self.assertRaisesRegex(visibility.RegistryError, "visibility must be exactly"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_invalid_ids_and_slugs_are_rejected(self) -> None:
        invalid_entries = (
            {"id": "contains whitespace", "slug": "casonk/example"},
            {"id": "R_valid", "slug": "not-a-slug"},
            {"id": "R_valid", "slug": "bad_owner/example"},
            {"id": "R_valid", "slug": "casonk/.."},
        )
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                self._write_pair(private=[entry])
                with self.assertRaises(visibility.RegistryError):
                    visibility.load_pair(self.private_path, self.public_path)

    def test_entries_must_be_deterministically_sorted(self) -> None:
        self._write_pair(
            private=[
                {"id": "R_z", "slug": "casonk/zulu"},
                {"id": "R_a", "slug": "casonk/alpha"},
            ]
        )
        with self.assertRaisesRegex(visibility.RegistryError, "not deterministically sorted"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_casefolded_slug_duplicates_are_rejected(self) -> None:
        self._write_pair(
            private=[
                {"id": "R_one", "slug": "casonk/example"},
                {"id": "R_two", "slug": "CasonK/Example"},
            ]
        )
        with self.assertRaisesRegex(visibility.RegistryError, "duplicate repository slug"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_cross_file_id_and_slug_conflicts_are_rejected(self) -> None:
        self._write_pair(
            private=[{"id": "R_same", "slug": "casonk/private-name"}],
            public=[{"id": "R_same", "slug": "casonk/public-name"}],
        )
        with self.assertRaisesRegex(visibility.RegistryError, "id appears in both"):
            visibility.load_pair(self.private_path, self.public_path)

        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/same-name"}],
            public=[{"id": "R_public", "slug": "CasonK/Same-Name"}],
        )
        with self.assertRaisesRegex(visibility.RegistryError, "slug appears in both"):
            visibility.load_pair(self.private_path, self.public_path)

    def test_record_private_adds_sorted_entry_and_advances_both_generations(self) -> None:
        self._write_pair(
            private=[{"id": "R_zulu", "slug": "casonk/zulu"}],
            public=[{"id": "R_public", "slug": "casonk/public"}],
        )

        added = visibility.record_private(
            str(self.private_path),
            str(self.public_path),
            "R_alpha",
            "casonk/alpha",
        )
        pair = visibility.load_pair(self.private_path, self.public_path)

        self.assertTrue(added)
        self.assertEqual(pair.generation, 8)
        self.assertEqual(
            [entry.slug for entry in pair.private.repositories],
            ["casonk/alpha", "casonk/zulu"],
        )
        self.assertEqual(pair.public.generation, 8)

    def test_record_private_is_idempotent_for_exact_identity(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/example"}],
        )
        before_private = self.private_path.read_bytes()
        before_public = self.public_path.read_bytes()

        added = visibility.record_private(
            str(self.private_path),
            str(self.public_path),
            "R_private",
            "casonk/example",
        )

        self.assertFalse(added)
        self.assertEqual(self.private_path.read_bytes(), before_private)
        self.assertEqual(self.public_path.read_bytes(), before_public)

    def test_record_private_rejects_public_or_identity_conflicts(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/private"}],
            public=[{"id": "R_public", "slug": "casonk/public"}],
        )
        attempts = (
            ("R_public", "casonk/public"),
            ("R_private", "casonk/renamed"),
            ("R_new", "casonk/private"),
        )
        for repository_id, slug in attempts:
            with self.subTest(repository_id=repository_id, slug=slug):
                with self.assertRaises(visibility.RegistryError):
                    visibility.record_private(
                        str(self.private_path),
                        str(self.public_path),
                        repository_id,
                        slug,
                    )

    def _write_fake_gh(self, payload: object, status: int = 0) -> Path:
        fake = self.root / "fake-gh"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' '{json.dumps(payload)}'\n"
            f"exit {status}\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        return fake

    def _write_observing_fake_gh(self, payload: object) -> tuple[Path, Path]:
        fake = self.root / "observing-fake-gh"
        log = self.root / "observing-fake-gh.log"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"${{GH_HOST-}}\" > '{log}'\n"
            f"printf '%s\\n' \"$@\" >> '{log}'\n"
            f"printf '%s\\n' '{json.dumps(payload)}'\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        return fake, log

    def test_reconcile_observed_moves_and_renames_an_exact_github_identity(self) -> None:
        repository_id = "R_synthetic_exact"
        source_slug = "sample-owner/old-public-name"
        target_slug = "sample-owner/new-private-name"
        self._write_pair(
            public=[{"id": repository_id, "slug": source_slug}],
        )
        fake, log = self._write_observing_fake_gh(
            {
                "id": repository_id,
                "nameWithOwner": target_slug,
                "visibility": "PRIVATE",
            }
        )

        with mock.patch.dict(
            os.environ,
            {"GH_HOST": "hostile-enterprise.invalid"},
            clear=False,
        ):
            changed = visibility.reconcile_observed(
                str(self.private_path),
                str(self.public_path),
                repository_id,
                source_slug,
                "public",
                target_slug,
                "private",
                gh_command=str(fake),
            )

        pair = visibility.load_pair(self.private_path, self.public_path)
        self.assertTrue(changed)
        self.assertEqual(pair.generation, 8)
        self.assertEqual(pair.private.generation, 8)
        self.assertEqual(pair.public.generation, 8)
        self.assertEqual(
            pair.private.repositories,
            (visibility.RepositoryEntry(repository_id, target_slug),),
        )
        self.assertEqual(pair.public.repositories, ())
        invocation = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(invocation[0], "github.com")
        self.assertEqual(
            invocation[1:],
            [
                "repo",
                "view",
                target_slug,
                "--json",
                "id,nameWithOwner,visibility",
            ],
        )

        before_private = self.private_path.read_bytes()
        before_public = self.public_path.read_bytes()
        changed = visibility.reconcile_observed(
            str(self.private_path),
            str(self.public_path),
            repository_id,
            source_slug,
            "public",
            target_slug,
            "private",
            gh_command=str(fake),
        )
        self.assertFalse(changed)
        self.assertEqual(self.private_path.read_bytes(), before_private)
        self.assertEqual(self.public_path.read_bytes(), before_public)

    def test_reconcile_observed_rejects_each_observation_mismatch_without_mutation(
        self,
    ) -> None:
        repository_id = "R_synthetic_source"
        source_slug = "sample-owner/source-name"
        target_slug = "sample-owner/target-name"
        observations = (
            (
                {
                    "id": "R_synthetic_other",
                    "nameWithOwner": target_slug,
                    "visibility": "PRIVATE",
                },
                "immutable ID",
            ),
            (
                {
                    "id": repository_id,
                    "nameWithOwner": "sample-owner/different-name",
                    "visibility": "PRIVATE",
                },
                "canonical GitHub slug",
            ),
            (
                {
                    "id": repository_id,
                    "nameWithOwner": target_slug,
                    "visibility": "PUBLIC",
                },
                "visibility",
            ),
        )
        for observation, message in observations:
            with self.subTest(message=message):
                self._write_pair(
                    public=[{"id": repository_id, "slug": source_slug}],
                )
                before_private = self.private_path.read_bytes()
                before_public = self.public_path.read_bytes()
                fake, _ = self._write_observing_fake_gh(observation)

                with self.assertRaisesRegex(visibility.RegistryError, message):
                    visibility.reconcile_observed(
                        str(self.private_path),
                        str(self.public_path),
                        repository_id,
                        source_slug,
                        "public",
                        target_slug,
                        "private",
                        gh_command=str(fake),
                    )

                self.assertEqual(self.private_path.read_bytes(), before_private)
                self.assertEqual(self.public_path.read_bytes(), before_public)

    def test_private_disclosure_audit_uses_index_blobs_and_sanitizes_findings(
        self,
    ) -> None:
        private_slug = "sample-owner/concealed-agent"
        private_name = private_slug.rsplit("/", 1)[1]
        self._write_pair(
            private=[{"id": "R_synthetic_private", "slug": private_slug}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        repository = self.root / "disclosure-worktree"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(repository)],
            check=True,
            capture_output=True,
        )
        tracked = repository / "tracked.txt"
        tracked.write_text("public material only\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "tracked.txt"],
            check=True,
            capture_output=True,
        )

        tracked.write_text(
            f"unstaged references: {private_slug} and {private_name}\n",
            encoding="utf-8",
        )
        visibility.audit_private_disclosures(pair, [str(repository)])

        subprocess.run(
            ["git", "-C", str(repository), "add", "tracked.txt"],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(visibility.PrivateDisclosureFailure) as raised:
            visibility.audit_private_disclosures(pair, [str(repository)])

        self.assertEqual(len(raised.exception.findings), 1)
        self.assertGreaterEqual(raised.exception.findings[0].count, 2)
        serialized_findings = repr(raised.exception.findings).casefold()
        self.assertNotIn(private_slug.casefold(), serialized_findings)
        self.assertNotIn(private_name.casefold(), serialized_findings)
        self.assertNotIn(private_slug.casefold(), str(raised.exception).casefold())
        self.assertNotIn(private_name.casefold(), str(raised.exception).casefold())

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = visibility.main(
                [
                    "audit-private-disclosures",
                    "--private",
                    str(self.private_path),
                    "--public",
                    str(self.public_path),
                    "--root",
                    str(repository),
                ]
            )
        rendered = stderr.getvalue().casefold()
        self.assertEqual(status, 1)
        self.assertNotIn(private_slug.casefold(), rendered)
        self.assertNotIn(private_name.casefold(), rendered)
        self.assertIn("root[1]", rendered)
        self.assertIn("file=", rendered)
        self.assertIn("count=", rendered)

    def test_private_disclosure_audit_scans_regular_and_gitlink_index_paths(
        self,
    ) -> None:
        private_slug = "sample-owner/concealed-agent"
        private_name = private_slug.rsplit("/", 1)[1]
        self._write_pair(
            private=[{"id": "R_synthetic_private", "slug": private_slug}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        repository = self.root / "path-worktree"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(repository)],
            check=True,
            capture_output=True,
        )
        disclosed_directory = repository / private_name
        disclosed_directory.mkdir()
        (disclosed_directory / "settings.json").write_text(
            '{"safe": true}\n',
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "add",
                f"{private_name}/settings.json",
            ],
            check=True,
            capture_output=True,
        )
        gitlink_path = f"vendor/{private_name}"
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{('1' * 40)},{gitlink_path}",
            ],
            check=True,
            capture_output=True,
        )

        with self.assertRaises(visibility.PrivateDisclosureFailure) as raised:
            visibility.audit_private_disclosures(pair, [str(repository)])

        self.assertEqual(len(raised.exception.findings), 2)
        rendered_paths = [finding.path for finding in raised.exception.findings]
        self.assertTrue(
            any(path == "<private>/settings.json" for path in rendered_paths)
        )
        self.assertTrue(any(path == "vendor/<private>" for path in rendered_paths))
        serialized_findings = repr(raised.exception.findings).casefold()
        self.assertNotIn(private_slug.casefold(), serialized_findings)
        self.assertNotIn(private_name.casefold(), serialized_findings)

    def test_remote_audit_matches_immutable_identity_and_visibility(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/example"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        fake = self._write_fake_gh(
            [
                {
                    "id": "R_private",
                    "nameWithOwner": "casonk/example",
                    "visibility": "PRIVATE",
                }
            ]
        )

        visibility.audit_pair(
            pair,
            gh_command=str(fake),
            portfolio_roots=[],
            skip_github=False,
        )

    def test_remote_audit_fails_closed_on_drift_or_lookup_failure(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/example"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        fake = self._write_fake_gh(
            [
                {
                    "id": "R_changed",
                    "nameWithOwner": "casonk/renamed",
                    "visibility": "PUBLIC",
                }
            ]
        )
        with self.assertRaises(visibility.AuditFailure) as raised:
            visibility.audit_pair(
                pair,
                gh_command=str(fake),
                portfolio_roots=[],
                skip_github=False,
            )
        self.assertGreaterEqual(len(raised.exception.failures), 2)

        fake = self._write_fake_gh({}, status=1)
        with self.assertRaises(visibility.AuditFailure):
            visibility.audit_pair(
                pair,
                gh_command=str(fake),
                portfolio_roots=[],
                skip_github=False,
            )

    def test_remote_audit_rejects_repository_missing_from_registry(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/example"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        fake = self._write_fake_gh(
            [
                {
                    "id": "R_private",
                    "nameWithOwner": "casonk/example",
                    "visibility": "PRIVATE",
                },
                {
                    "id": "R_unknown",
                    "nameWithOwner": "casonk/outside-wrapper",
                    "visibility": "PRIVATE",
                },
            ]
        )

        with self.assertRaises(visibility.AuditFailure) as raised:
            visibility.audit_pair(
                pair,
                gh_command=str(fake),
                portfolio_roots=[],
                skip_github=False,
            )

        self.assertIn("absent from the registry", raised.exception.failures[0])

    def _init_local_repo(self, relative_path: str, origin: str | None) -> Path:
        repository = self.root / "portfolio" / relative_path
        repository.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", str(repository)],
            check=True,
            capture_output=True,
        )
        if origin is not None:
            subprocess.run(
                ["git", "-C", str(repository), "remote", "add", "origin", origin],
                check=True,
                capture_output=True,
            )
        return repository

    def test_insecure_github_remote_transports_are_not_canonical(self) -> None:
        for remote in (
            "http://github.com/casonk/example.git",
            "git://github.com/casonk/example.git",
        ):
            with self.subTest(remote=remote):
                self.assertIsNone(visibility._normalize_github_remote(remote))

        for remote in (
            "https://github.com/casonk/example.git",
            "ssh://git@github.com/casonk/example.git",
            "git@github.com:casonk/example.git",
        ):
            with self.subTest(remote=remote):
                self.assertEqual(
                    visibility._normalize_github_remote(remote),
                    "casonk/example",
                )

    def test_local_audit_accepts_classified_github_remotes(self) -> None:
        self._write_pair(
            public=[{"id": "R_public", "slug": "casonk/example"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        self._init_local_repo("util-repos/example", "git@github.com:casonk/example.git")

        visibility.audit_pair(
            pair,
            gh_command="unused",
            portfolio_roots=[str(self.root / "portfolio")],
            skip_github=True,
        )

    def test_local_audit_rejects_unclassified_or_originless_repositories(self) -> None:
        self._write_pair()
        pair = visibility.load_pair(self.private_path, self.public_path)
        self._init_local_repo(
            "util-repos/unclassified",
            "https://github.com/casonk/unclassified.git",
        )
        self._init_local_repo("util-repos/local-only", None)

        with self.assertRaises(visibility.AuditFailure) as raised:
            visibility.audit_pair(
                pair,
                gh_command="unused",
                portfolio_roots=[str(self.root / "portfolio")],
                skip_github=True,
            )

        self.assertEqual(len(raised.exception.failures), 2)

    def test_local_audit_checks_push_urls_and_every_remote(self) -> None:
        self._write_pair(
            public=[{"id": "R_public", "slug": "casonk/classified"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        repository = self._init_local_repo(
            "util-repos/example",
            "https://github.com/casonk/classified.git",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "set-url",
                "--push",
                "origin",
                "git@github.com:casonk/unclassified-push.git",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "add",
                "backup",
                "https://github.com/casonk/unclassified-backup.git",
            ],
            check=True,
            capture_output=True,
        )

        with self.assertRaises(visibility.AuditFailure) as raised:
            visibility.audit_pair(
                pair,
                gh_command="unused",
                portfolio_roots=[str(self.root / "portfolio")],
                skip_github=True,
            )

        rendered = "\n".join(raised.exception.failures)
        self.assertIn("unclassified-push", rendered)
        self.assertIn("unclassified-backup", rendered)

    def test_local_audit_rejects_multiple_registered_remote_identities(self) -> None:
        self._write_pair(
            private=[{"id": "R_private", "slug": "casonk/private-target"}],
            public=[{"id": "R_public", "slug": "casonk/public-target"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        repository = self._init_local_repo(
            "util-repos/example",
            "https://github.com/casonk/private-target.git",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "set-url",
                "--push",
                "origin",
                "git@github.com:casonk/public-target.git",
            ],
            check=True,
            capture_output=True,
        )

        with self.assertRaises(visibility.AuditFailure) as raised:
            visibility.audit_pair(
                pair,
                gh_command="unused",
                portfolio_roots=[str(self.root / "portfolio")],
                skip_github=True,
            )

        rendered = "\n".join(raised.exception.failures)
        self.assertIn("multiple registered identities", rendered)
        self.assertIn("private-target (private)", rendered)
        self.assertIn("public-target (public)", rendered)

    def test_local_audit_rejects_registered_backup_for_another_identity(self) -> None:
        self._write_pair(
            public=[
                {"id": "R_backup", "slug": "casonk/backup-target"},
                {"id": "R_primary", "slug": "casonk/primary-target"},
            ],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        repository = self._init_local_repo(
            "util-repos/example",
            "https://github.com/casonk/primary-target.git",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "add",
                "backup",
                "git@github.com:casonk/backup-target.git",
            ],
            check=True,
            capture_output=True,
        )

        with self.assertRaises(visibility.AuditFailure) as raised:
            visibility.audit_pair(
                pair,
                gh_command="unused",
                portfolio_roots=[str(self.root / "portfolio")],
                skip_github=True,
            )

        self.assertIn(
            "multiple registered identities",
            "\n".join(raised.exception.failures),
        )

    def test_local_audit_accepts_multiple_urls_for_the_same_identity(self) -> None:
        self._write_pair(
            public=[{"id": "R_public", "slug": "casonk/example"}],
        )
        pair = visibility.load_pair(self.private_path, self.public_path)
        repository = self._init_local_repo(
            "util-repos/example",
            "https://github.com/casonk/example.git",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "set-url",
                "--push",
                "--add",
                "origin",
                "git@github.com:casonk/example.git",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "add",
                "backup",
                "ssh://git@github.com/casonk/example.git",
            ],
            check=True,
            capture_output=True,
        )

        visibility.audit_pair(
            pair,
            gh_command="unused",
            portfolio_roots=[str(self.root / "portfolio")],
            skip_github=True,
        )


if __name__ == "__main__":
    unittest.main()
