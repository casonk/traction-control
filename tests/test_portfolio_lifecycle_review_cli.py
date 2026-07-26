"""Focused real-Git and CLI regressions for portfolio lifecycle review."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "scripts"
REVIEWER = SCRIPTS / "portfolio_lifecycle_review.py"
sys.path.insert(0, str(SCRIPTS))

import portfolio_lifecycle_review as lifecycle  # noqa: E402
import portfolio_materializer as materializer  # noqa: E402
import repository_visibility as visibility  # noqa: E402


PRIVATE_ID = "NODE_PRIVATE_TARGET"
PRIVATE_SLUG = "synthetic-owner/private-target"
MISSING_ID = "NODE_MISSING_TARGET"
MISSING_SLUG = "synthetic-owner/missing-target"
SOURCE_ID = "NODE_PUBLIC_SOURCE"
SOURCE_SLUG = "synthetic-owner/public-source"


class PortfolioLifecycleReviewCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.portfolio = self.root / "portfolio"
        self.control = self.root / "control"
        self.portfolio.mkdir(mode=0o700)
        self.control.mkdir(mode=0o700)
        self.private_path = self.control / "private.local.json"
        self.public_path = self.control / "public.local.json"
        self.catalog_path = self.control / "portfolio.local.json"
        self._write_registry()
        self._write_catalog()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(
        self,
        *command: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            env=environment,
        )

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _write_registry(self) -> None:
        common = {
            "schema_version": 1,
            "registry_id": "synthetic-lifecycle-test",
            "generation": 7,
        }
        self._write_json(
            self.private_path,
            {
                **common,
                "visibility": "private",
                "repositories": [
                    {
                        "id": PRIVATE_ID,
                        "slug": PRIVATE_SLUG,
                    }
                ],
            },
        )
        self._write_json(
            self.public_path,
            {
                **common,
                "visibility": "public",
                "repositories": [
                    {
                        "id": MISSING_ID,
                        "slug": MISSING_SLUG,
                    },
                    {
                        "id": SOURCE_ID,
                        "slug": SOURCE_SLUG,
                    },
                ],
            },
        )

    def _catalog_rows(
        self,
        *,
        desired_presence: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        presence = desired_presence or {}
        rows = [
            {
                "repository_id": MISSING_ID,
                "relative_path": "repos/missing-target",
                "lifecycle": "active",
                "sync_policy": "fetch-only",
                "desired_presence": presence.get(MISSING_ID, "checkout"),
            },
            {
                "repository_id": PRIVATE_ID,
                "relative_path": "repos/private-target",
                "lifecycle": "active",
                "sync_policy": "fetch-only",
                "desired_presence": presence.get(PRIVATE_ID, "checkout"),
            },
            {
                "repository_id": SOURCE_ID,
                "relative_path": "repos/public-source",
                "lifecycle": "active",
                "sync_policy": "fetch-only",
                "desired_presence": presence.get(SOURCE_ID, "checkout"),
            },
        ]
        return sorted(
            rows,
            key=lambda row: (
                row["relative_path"].casefold(),
                row["repository_id"],
            ),
        )

    def _write_catalog(
        self,
        *,
        desired_presence: dict[str, str] | None = None,
    ) -> None:
        self._write_json(
            self.catalog_path,
            {
                "schema_version": 1,
                "registry_id": "synthetic-lifecycle-test",
                "registry_generation": 7,
                "catalog_generation": 3,
                "repositories": self._catalog_rows(
                    desired_presence=desired_presence
                ),
            },
        )

    def _entry_path(self, repository_id: str) -> Path:
        paths = {
            MISSING_ID: "repos/missing-target",
            PRIVATE_ID: "repos/private-target",
            SOURCE_ID: "repos/public-source",
        }
        return self.portfolio / paths[repository_id]

    def _slug(self, repository_id: str) -> str:
        return {
            MISSING_ID: MISSING_SLUG,
            PRIVATE_ID: PRIVATE_SLUG,
            SOURCE_ID: SOURCE_SLUG,
        }[repository_id]

    def _create_checkout(
        self,
        repository_id: str,
        *,
        files: dict[str, str] | None = None,
    ) -> Path:
        checkout = self._entry_path(repository_id)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        self._run("git", "init", "-q", "-b", "main", str(checkout))
        self._run("git", "-C", str(checkout), "config", "user.name", "Test User")
        self._run(
            "git",
            "-C",
            str(checkout),
            "config",
            "user.email",
            "test@example.invalid",
        )
        tracked = {"README.md": f"# {repository_id}\n"}
        if files:
            tracked.update(files)
        for relative_path, content in tracked.items():
            destination = checkout / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        self._run("git", "-C", str(checkout), "add", "--all")
        self._run("git", "-C", str(checkout), "commit", "-qm", "initial")
        self._run(
            "git",
            "-C",
            str(checkout),
            "remote",
            "add",
            "origin",
            f"https://github.com/{self._slug(repository_id)}.git",
        )
        return checkout

    def _create_all_checkouts(self) -> None:
        for repository_id in (MISSING_ID, PRIVATE_ID, SOURCE_ID):
            self._create_checkout(repository_id)

    def _load_control_plane(
        self,
    ) -> tuple[visibility.RegistryPair, materializer.CatalogDocument]:
        pair = visibility.load_pair(self.private_path, self.public_path)
        catalog = materializer.load_catalog(self.catalog_path, pair)
        return pair, catalog

    def _scan(self) -> lifecycle.ScanResult:
        pair, catalog = self._load_control_plane()
        return lifecycle.scan_portfolio(pair, catalog, self.portfolio)

    def _run_cli(
        self,
        output: Path,
        *,
        plan: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(REVIEWER),
            "--private",
            str(self.private_path),
            "--public",
            str(self.public_path),
            "--catalog",
            str(self.catalog_path),
            "--portfolio-root",
            str(self.portfolio),
            "--output",
            str(output),
        ]
        if plan is not None:
            command.extend(("--plan", str(plan)))
        return self._run(*command, check=False)

    def test_only_canonical_root_gitmodules_is_a_dependency_manifest(self) -> None:
        self.assertTrue(lifecycle._is_manifest(".gitmodules"))
        self.assertFalse(lifecycle._is_manifest(".GITMODULES"))
        self.assertFalse(lifecycle._is_manifest("nested/.gitmodules"))
        self._create_checkout(MISSING_ID)
        self._create_checkout(PRIVATE_ID)
        source = self._create_checkout(
            SOURCE_ID,
            files={
                ".gitmodules": (
                    "[submodule \"canonical\"]\n"
                    "\tpath = canonical\n"
                    f"\turl = https://github.com/{PRIVATE_SLUG}.git\n"
                ),
                "nested/.gitmodules": (
                    "This nested file is ordinary tracked text referencing "
                    f"{PRIVATE_SLUG}\n"
                ),
            },
        )
        self.assertTrue((source / ".gitmodules").is_file())

        scan = self._scan()

        source_manifests = {
            item["file"]
            for item in scan.manifests
            if item["source_repository_id"] == SOURCE_ID
        }
        self.assertEqual(source_manifests, {".gitmodules"})
        evidence_by_file = {
            reference["file"]: reference["evidence"]
            for reference in scan.references
            if reference["source_repository_id"] == SOURCE_ID
            and reference["target_repository_id"] == PRIVATE_ID
        }
        self.assertEqual(evidence_by_file[".gitmodules"], "dependency-manifest")
        self.assertEqual(
            evidence_by_file["nested/.gitmodules"],
            "tracked-text-reference",
        )

    def test_git_control_marker_must_be_a_real_root_directory(self) -> None:
        self._create_checkout(MISSING_ID)

        source_base = self.root / "source-base"
        self._run("git", "init", "-q", "-b", "main", str(source_base))
        self._run(
            "git",
            "-C",
            str(source_base),
            "config",
            "user.name",
            "Test User",
        )
        self._run(
            "git",
            "-C",
            str(source_base),
            "config",
            "user.email",
            "test@example.invalid",
        )
        (source_base / "README.md").write_text("# source base\n", encoding="utf-8")
        self._run("git", "-C", str(source_base), "add", "README.md")
        self._run("git", "-C", str(source_base), "commit", "-qm", "initial")
        self._run(
            "git",
            "-C",
            str(source_base),
            "remote",
            "add",
            "origin",
            f"https://github.com/{SOURCE_SLUG}.git",
        )
        linked_checkout = self._entry_path(SOURCE_ID)
        linked_checkout.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            "git",
            "-C",
            str(source_base),
            "worktree",
            "add",
            "-q",
            "-b",
            "linked-test",
            str(linked_checkout),
        )
        self.assertTrue((linked_checkout / ".git").is_file())

        private_checkout = self._create_checkout(PRIVATE_ID)
        git_directory = private_checkout / ".git"
        relocated_git_directory = self.root / "private-git-control"
        git_directory.rename(relocated_git_directory)
        git_directory.symlink_to(relocated_git_directory, target_is_directory=True)
        self.assertTrue(git_directory.is_symlink())

        scan = self._scan()
        blockers_by_repository = {
            repository_id: {
                issue["code"]
                for issue in scan.blockers
                if issue.get("repository_id") == repository_id
            }
            for repository_id in (SOURCE_ID, PRIVATE_ID)
        }
        self.assertIn(
            "checkout-identity-mismatch",
            blockers_by_repository[SOURCE_ID],
        )
        self.assertIn("symlink-git-control", blockers_by_repository[PRIVATE_ID])
        reports = {
            report["repository_id"]: report
            for report in scan.repositories
            if report["repository_id"] is not None
        }
        self.assertIsNone(reports[SOURCE_ID]["source_commit"])
        self.assertIsNone(reports[PRIVATE_ID]["source_commit"])

    def test_staged_and_unstaged_tracked_changes_are_not_attributed_to_head(
        self,
    ) -> None:
        missing = self._create_checkout(MISSING_ID)
        self._create_checkout(PRIVATE_ID)
        source = self._create_checkout(SOURCE_ID)

        (source / "README.md").write_text(
            f"unstaged reference to {PRIVATE_SLUG}\n",
            encoding="utf-8",
        )
        (missing / "README.md").write_text(
            f"staged reference to {PRIVATE_SLUG}\n",
            encoding="utf-8",
        )
        self._run("git", "-C", str(missing), "add", "README.md")

        scan = self._scan()

        dirty_ids = {
            issue["repository_id"]
            for issue in scan.blockers
            if issue["code"] == "dirty-tracked-checkout"
        }
        self.assertEqual(dirty_ids, {MISSING_ID, SOURCE_ID})
        reports = {
            report["repository_id"]: report
            for report in scan.repositories
            if report["repository_id"] is not None
        }
        for repository_id in dirty_ids:
            self.assertEqual(
                reports[repository_id]["status"],
                "not-scanned-dirty-tracked",
            )
            self.assertIsNone(reports[repository_id]["source_commit"])
            self.assertIsNone(reports[repository_id]["tracked_file_count"])
        self.assertFalse(
            any(
                reference["source_repository_id"] in dirty_ids
                for reference in scan.references
            )
        )

    def test_assume_unchanged_and_skip_worktree_cannot_hide_mutable_bytes(
        self,
    ) -> None:
        assumed = self._create_checkout(MISSING_ID)
        self._create_checkout(PRIVATE_ID)
        skipped = self._create_checkout(SOURCE_ID)
        self._run(
            "git",
            "-C",
            str(assumed),
            "update-index",
            "--assume-unchanged",
            "README.md",
        )
        self._run(
            "git",
            "-C",
            str(skipped),
            "update-index",
            "--skip-worktree",
            "README.md",
        )
        (assumed / "README.md").write_text(
            f"hidden worktree reference to {PRIVATE_SLUG}\n",
            encoding="utf-8",
        )
        (skipped / "README.md").write_text(
            f"hidden worktree reference to {PRIVATE_SLUG}\n",
            encoding="utf-8",
        )

        scan = self._scan()

        dirty_ids = {
            issue["repository_id"]
            for issue in scan.blockers
            if issue["code"] == "dirty-tracked-checkout"
        }
        self.assertEqual(dirty_ids, {MISSING_ID, SOURCE_ID})
        self.assertFalse(
            any(
                reference["source_repository_id"] in dirty_ids
                for reference in scan.references
            )
        )

    def test_head_change_during_blob_scan_discards_stale_evidence(self) -> None:
        self._create_checkout(MISSING_ID)
        self._create_checkout(PRIVATE_ID)
        source = self._create_checkout(
            SOURCE_ID,
            files={"requirements.txt": f"dependency = {PRIVATE_SLUG}\n"},
        )
        original_reader = lifecycle._read_tree_blobs
        changed = False

        def read_then_move_head(
            snapshot: lifecycle._RepositorySnapshot,
        ) -> dict[str, tuple[str, bytes | None, int | None]]:
            nonlocal changed
            result = original_reader(snapshot)
            if snapshot.repository_id == SOURCE_ID and not changed:
                changed = True
                self._run(
                    "git",
                    "-C",
                    str(source),
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "concurrent head movement",
                )
            return result

        with mock.patch.object(
            lifecycle,
            "_read_tree_blobs",
            side_effect=read_then_move_head,
        ):
            scan = self._scan()

        self.assertTrue(changed)
        self.assertTrue(
            any(
                issue["code"] == "repository-changed-during-scan"
                and issue.get("repository_id") == SOURCE_ID
                for issue in scan.blockers
            )
        )
        self.assertFalse(
            any(
                reference["source_repository_id"] == SOURCE_ID
                for reference in scan.references
            )
        )
        source_report = next(
            report
            for report in scan.repositories
            if report["repository_id"] == SOURCE_ID
        )
        self.assertEqual(
            source_report["status"],
            "not-scanned-concurrent-change",
        )
        self.assertIsNone(source_report["source_commit"])

    def test_ambient_fsmonitor_and_filter_commands_cannot_execute_or_hide_dirt(
        self,
    ) -> None:
        self._create_checkout(MISSING_ID)
        self._create_checkout(PRIVATE_ID)
        source = self._create_checkout(
            SOURCE_ID,
            files={".gitattributes": "README.md filter=hostile\n"},
        )
        marker = self.root / "ambient-git-command-ran"
        command = self.root / "hostile-git-command.sh"
        command.write_text(
            "#!/bin/sh\n"
            f"printf invoked > '{marker}'\n"
            "cat\n",
            encoding="utf-8",
        )
        command.chmod(0o700)
        malicious_config = self.root / "malicious-gitconfig"
        malicious_config.write_text(
            "[core]\n"
            f"\tfsmonitor = {command}\n"
            "[filter \"hostile\"]\n"
            f"\tclean = {command}\n"
            "\trequired = true\n",
            encoding="utf-8",
        )
        self._run(
            "git",
            "-C",
            str(source),
            "config",
            "core.fsmonitor",
            str(command),
        )
        self._run(
            "git",
            "-C",
            str(source),
            "config",
            "filter.hostile.clean",
            str(command),
        )
        self._run(
            "git",
            "-C",
            str(source),
            "config",
            "filter.hostile.required",
            "true",
        )
        (source / "README.md").write_text(
            f"mutable reference to {PRIVATE_SLUG}\n",
            encoding="utf-8",
        )
        hostile_environment = {
            "GIT_CONFIG_GLOBAL": str(malicious_config),
            "GIT_CONFIG_SYSTEM": str(malicious_config),
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": str(command),
            "GIT_CONFIG_KEY_1": "filter.hostile.clean",
            "GIT_CONFIG_VALUE_1": str(command),
        }

        with mock.patch.dict(os.environ, hostile_environment, clear=False):
            scan = self._scan()

        self.assertFalse(marker.exists())
        self.assertTrue(
            any(
                issue["code"] == "dirty-tracked-checkout"
                and issue.get("repository_id") == SOURCE_ID
                for issue in scan.blockers
            )
        )
        self.assertFalse(
            any(
                reference["source_repository_id"] == SOURCE_ID
                for reference in scan.references
            )
        )

    def test_control_plane_change_during_scan_prevents_report(self) -> None:
        self._create_all_checkouts()
        output = self.root / "reports" / "review.local.json"
        original_scan = lifecycle.scan_portfolio

        def scan_then_change_catalog(
            pair: visibility.RegistryPair,
            catalog: materializer.CatalogDocument,
            portfolio_root: Path,
        ) -> lifecycle.ScanResult:
            result = original_scan(pair, catalog, portfolio_root)
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            payload["catalog_generation"] += 1
            self._write_json(self.catalog_path, payload)
            return result

        arguments = [
            "--private",
            str(self.private_path),
            "--public",
            str(self.public_path),
            "--catalog",
            str(self.catalog_path),
            "--portfolio-root",
            str(self.portfolio),
            "--output",
            str(output),
        ]
        error_stream = mock.MagicMock()
        with (
            mock.patch.object(
                lifecycle,
                "scan_portfolio",
                side_effect=scan_then_change_catalog,
            ),
            mock.patch.object(sys, "stderr", error_stream),
        ):
            result = lifecycle.main(arguments)

        self.assertEqual(result, 2)
        self.assertFalse(output.exists())

    def test_desired_absent_states_always_leave_coverage_blockers(self) -> None:
        self._write_catalog(
            desired_presence={
                MISSING_ID: "absent",
                PRIVATE_ID: "absent",
                SOURCE_ID: "absent",
            }
        )
        source_path = self._entry_path(SOURCE_ID)
        source_path.mkdir(parents=True)
        symlink_target = self.root / "symlink-target"
        symlink_target.mkdir()
        private_path = self._entry_path(PRIVATE_ID)
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.symlink_to(symlink_target, target_is_directory=True)

        scan = self._scan()
        codes_by_repository = {
            repository_id: [
                issue["code"]
                for issue in scan.blockers
                if issue.get("repository_id") == repository_id
            ]
            for repository_id in (MISSING_ID, PRIVATE_ID, SOURCE_ID)
        }

        self.assertIn(
            "absent-repository-evidence-unavailable",
            codes_by_repository[MISSING_ID],
        )
        self.assertNotIn(
            "configured-absent-path-present",
            codes_by_repository[MISSING_ID],
        )
        self.assertIn(
            "configured-absent-path-present",
            codes_by_repository[SOURCE_ID],
        )
        self.assertIn("unsafe-checkout-path", codes_by_repository[PRIVATE_ID])
        self.assertIn(
            "configured-absent-path-present",
            codes_by_repository[PRIVATE_ID],
        )
        self.assertTrue(scan.blockers)

    def test_public_source_reference_to_private_target_is_a_blocker(self) -> None:
        self._create_checkout(MISSING_ID)
        self._create_checkout(PRIVATE_ID)
        self._create_checkout(
            SOURCE_ID,
            files={
                "requirements.txt": (
                    f"dependency @ git+https://github.com/{PRIVATE_SLUG}.git\n"
                )
            },
        )

        scan = self._scan()

        matching_references = [
            reference
            for reference in scan.references
            if reference["source_repository_id"] == SOURCE_ID
            and reference["target_repository_id"] == PRIVATE_ID
            and reference["file"] == "requirements.txt"
        ]
        self.assertEqual(len(matching_references), 1)
        self.assertEqual(
            matching_references[0]["evidence"],
            "dependency-manifest",
        )
        self.assertTrue(
            any(
                issue["code"] == "public-to-private-reference"
                and issue.get("repository_id") == SOURCE_ID
                for issue in scan.blockers
            )
        )

    def test_action_targeting_unavailable_repository_is_blocked(self) -> None:
        self._create_checkout(PRIVATE_ID)
        self._create_checkout(SOURCE_ID)
        plan_path = self.control / "plan.local.json"
        self._write_json(
            plan_path,
            {
                "schema_version": 1,
                "actions": [
                    {
                        "action": "make-private",
                        "target_repository_id": MISSING_ID,
                        "reason": "Evaluate access boundaries before retirement.",
                    }
                ],
            },
        )
        output = self.root / "reports" / "review.local.json"

        result = self._run_cli(output, plan=plan_path)

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(output.read_text(encoding="utf-8"))
        review = payload["proposed_actions"][0]
        self.assertEqual(review["disposition"], "blocked")
        self.assertTrue(
            any("unavailable" in blocker for blocker in review["blockers"])
        )
        self.assertEqual(payload["summary"]["blocked_action_count"], 1)

    def test_action_only_blocker_causes_nonzero_cli_exit(self) -> None:
        self._create_all_checkouts()
        plan_path = self.control / "unsafe-action-only-plan.local.json"
        self._write_json(
            plan_path,
            {
                "schema_version": 1,
                "actions": [
                    {
                        "action": "archive",
                        "target_repository_id": SOURCE_ID,
                        "reason": "Review a public repository for retirement.",
                    }
                ],
            },
        )
        output = self.root / "reports" / "review.local.json"

        result = self._run_cli(output, plan=plan_path)

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["blocker_count"], 0)
        self.assertEqual(payload["summary"]["blocked_action_count"], 1)
        self.assertEqual(
            payload["proposed_actions"][0]["disposition"],
            "blocked",
        )

    def test_cli_success_secures_output_file_and_parent_directory(self) -> None:
        self._create_all_checkouts()
        output_parent = self.root / "reports"
        output_parent.mkdir(mode=0o755)
        output = output_parent / "review.local.json"

        result = self._run_cli(output)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("review complete: 3 repositories", result.stdout)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output_parent.stat().st_mode), 0o700)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["blocker_count"], 0)
        self.assertEqual(
            payload["safety"],
            {
                "changes_applied": False,
                "github_mutations": False,
                "git_mutations": False,
            },
        )

    def test_cli_refuses_tracked_unignored_and_symlink_outputs(self) -> None:
        self._create_all_checkouts()
        output_worktree = self.root / "output-worktree"
        self._run("git", "init", "-q", "-b", "main", str(output_worktree))
        self._run(
            "git",
            "-C",
            str(output_worktree),
            "config",
            "user.name",
            "Test User",
        )
        self._run(
            "git",
            "-C",
            str(output_worktree),
            "config",
            "user.email",
            "test@example.invalid",
        )

        tracked_output = output_worktree / "tracked.local.json"
        self._write_json(tracked_output, {"existing": True})
        self._run(
            "git",
            "-C",
            str(output_worktree),
            "add",
            "tracked.local.json",
        )
        self._run(
            "git",
            "-C",
            str(output_worktree),
            "commit",
            "-qm",
            "track forbidden output",
        )
        tracked_result = self._run_cli(tracked_output)
        self.assertEqual(tracked_result.returncode, 2)
        self.assertIn("must not be tracked", tracked_result.stderr)

        unignored_output = output_worktree / "unignored.local.json"
        unignored_result = self._run_cli(unignored_output)
        self.assertEqual(unignored_result.returncode, 2)
        self.assertIn("must be ignored", unignored_result.stderr)

        symlink_target = self.root / "existing-output-target.json"
        self._write_json(symlink_target, {"existing": True})
        symlink_output = self.root / "symlink-output.local.json"
        symlink_output.symlink_to(symlink_target)
        symlink_result = self._run_cli(symlink_output)
        self.assertEqual(symlink_result.returncode, 2)
        self.assertIn("non-symlink", symlink_result.stderr)
        self.assertEqual(
            json.loads(symlink_target.read_text(encoding="utf-8")),
            {"existing": True},
        )

    def test_cli_rejects_malformed_plan_with_usage_exit_code(self) -> None:
        self._create_all_checkouts()
        plan_path = self.control / "malformed-plan.local.json"
        self._write_json(
            plan_path,
            {
                "schema_version": 1,
                "actions": "not-an-array",
            },
        )
        output = self.root / "reports" / "review.local.json"

        result = self._run_cli(output, plan=plan_path)

        self.assertEqual(result.returncode, 2)
        self.assertIn("actions must be a JSON array", result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
