from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import archility_pr_deploy as deploy  # noqa: E402
import portfolio_materializer as materializer  # noqa: E402
import repository_visibility as visibility  # noqa: E402


class ArchilityPrDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_entry = visibility.RepositoryEntry("R_private", "owner/private-repo")
        self.public_entry = visibility.RepositoryEntry("R_public", "owner/public-repo")
        self.pair = visibility.RegistryPair(
            private=visibility.RegistryDocument(
                Path("private.local.json"),
                "portfolio-test",
                3,
                "private",
                (self.private_entry,),
            ),
            public=visibility.RegistryDocument(
                Path("public.local.json"),
                "portfolio-test",
                3,
                "public",
                (self.public_entry,),
            ),
        )

    def test_git_environment_uses_gh_for_authenticated_https_remotes(self):
        environment = deploy._git_environment()
        helper_values = [
            environment[key]
            for key in sorted(environment)
            if key.startswith("GIT_CONFIG_VALUE_")
        ]

        self.assertIn("!gh auth git-credential", helper_values)
        self.assertEqual(environment["GH_HOST"], "github.com")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")

    def _catalog_entry(
        self,
        repository_id: str,
        *,
        lifecycle: str = "active",
        sync_policy: str = "fetch-only",
        desired_presence: str = "checkout",
    ) -> materializer.CatalogEntry:
        return materializer.CatalogEntry(
            repository_id=repository_id,
            relative_path=f"repos/{repository_id}",
            lifecycle=lifecycle,
            sync_policy=sync_policy,
            desired_presence=desired_presence,
        )

    def test_eligible_entries_only_includes_active_fetch_only_checkouts(self):
        catalog = materializer.CatalogDocument(
            path=Path("portfolio.local.json"),
            registry_id="portfolio-test",
            registry_generation=3,
            catalog_generation=2,
            repositories=(
                self._catalog_entry("R_private"),
                self._catalog_entry("R_public"),
                self._catalog_entry("R_private", lifecycle="archived"),
                self._catalog_entry("R_public", sync_policy="manual"),
                self._catalog_entry("R_public", desired_presence="absent"),
            ),
        )

        candidates = deploy._eligible_entries(self.pair, catalog)

        self.assertEqual(
            [(entry.repository_id, visibility_name) for entry, visibility_name, _ in candidates],
            [("R_private", "private"), ("R_public", "public")],
        )

    def test_deployer_skips_catalog_entry_without_local_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(deploy, "_remote_urls") as remote_urls:
                result = deploy._deploy_one(
                    portfolio_root=Path(temporary),
                    catalog_entry=self._catalog_entry("R_private"),
                    visibility_name="private",
                    registry_entry=self.private_entry,
                    gh_command="gh",
                    archility_command=("archility",),
                )

        self.assertEqual(result, "skipped")
        remote_urls.assert_not_called()

    def test_github_metadata_must_match_registry_and_has_valid_default_branch(self):
        with mock.patch.object(
            deploy,
            "_github_json",
            return_value={
                "id": "R_public",
                "nameWithOwner": "owner/public-repo",
                "visibility": "PUBLIC",
                "isArchived": False,
                "defaultBranchRef": {"name": "main"},
            },
        ):
            self.assertEqual(
                deploy._repository_metadata("gh", self.public_entry),
                ("public", "main"),
            )

        with mock.patch.object(
            deploy,
            "_github_json",
            return_value={
                "id": "R_other",
                "nameWithOwner": "owner/public-repo",
                "visibility": "PUBLIC",
                "isArchived": False,
                "defaultBranchRef": {"name": "main"},
            },
        ):
            with self.assertRaisesRegex(deploy.DeploymentError, "ID differs"):
                deploy._repository_metadata("gh", self.public_entry)

    def test_open_archility_pr_detection_ignores_other_prs(self):
        with mock.patch.object(
            deploy,
            "_github_json",
            return_value=[
                {"headRefName": "feature/something"},
                {"headRefName": "automation/archility-diagrams-abc123"},
            ],
        ):
            self.assertTrue(deploy._open_archility_pr_exists("gh", "owner/repo", "main"))

        with mock.patch.object(deploy, "_github_json", return_value=[]):
            self.assertFalse(deploy._open_archility_pr_exists("gh", "owner/repo", "main"))

    def test_stage_diagram_changes_leaves_everything_else_unstaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "docs" / "diagrams").mkdir(parents=True)
            (repository / "docs" / "diagrams" / "architecture.svg").write_text(
                "old\n", encoding="utf-8"
            )
            (repository / "README.md").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Test"], check=True
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "core.hooksPath", "/dev/null"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "--quiet", "-m", "initial"],
                check=True,
            )
            (repository / "docs" / "diagrams" / "architecture.svg").write_text(
                "new\n", encoding="utf-8"
            )
            (repository / "README.md").write_text("after\n", encoding="utf-8")

            staged = deploy._stage_diagram_changes(repository)
            unstaged = subprocess.run(
                ["git", "-C", str(repository), "diff", "--name-only"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.splitlines()

            self.assertEqual(staged, ["docs/diagrams/architecture.svg"])
            self.assertEqual(unstaged, ["README.md"])


if __name__ == "__main__":
    unittest.main()
