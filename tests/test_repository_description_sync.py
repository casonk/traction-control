"""Tests for registered-repository description synchronization."""

from __future__ import annotations

import base64
import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repository_description_sync  # noqa: E402


def registry_pair(*, visibility: str = "public", slug: str = "owner/repo") -> SimpleNamespace:
    entry = SimpleNamespace(slug=slug, repository_id="R_test")
    document = SimpleNamespace(repositories=(entry,))
    empty = SimpleNamespace(repositories=())
    return SimpleNamespace(
        private=document if visibility == "private" else empty,
        public=document if visibility == "public" else empty,
    )


def remote(*, description: str = "", archived: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        owner="owner",
        name="repo",
        visibility="public",
        archived=archived,
        default_branch="main",
        description=description,
        slug="owner/repo",
    )


def encoded_settings(description: str = "A useful description.") -> dict[str, str]:
    text = f'[repository]\ndescription = "{description}"\n'
    return {
        "encoding": "base64",
        "content": base64.b64encode(text.encode()).decode(),
    }


class DescriptionSyncTests(unittest.TestCase):
    def test_applies_only_the_description_from_merged_settings(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                side_effect=[encoded_settings(), {}],
            ) as gh_api,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = repository_description_sync.sync_pair(registry_pair())

        self.assertEqual(result, 0)
        self.assertEqual(
            gh_api.call_args_list,
            [
                call(
                    "repos/owner/repo/contents/.github/repository.toml",
                    "-f",
                    "ref=main",
                ),
                call(
                    "-X",
                    "PATCH",
                    "repos/owner/repo",
                    "-f",
                    "description=A useful description.",
                ),
            ],
        )
        self.assertIn("1 applied", output.getvalue())

    def test_archived_missing_description_is_reported_without_mutation(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(archived=True),
            ),
            patch.object(repository_description_sync.repo_settings, "gh_api") as gh_api,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = repository_description_sync.sync_pair(registry_pair())

        self.assertEqual(result, 1)
        self.assertIn("archived repository has no GitHub description", output.getvalue())
        gh_api.assert_not_called()

    def test_missing_canonical_settings_are_reported(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                side_effect=RuntimeError("HTTP 404: Not Found"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = repository_description_sync.sync_pair(registry_pair())

        self.assertEqual(result, 1)
        self.assertIn(".github/repository.toml is missing", output.getvalue())

    def test_dry_run_does_not_patch_github(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                return_value=encoded_settings(),
            ) as gh_api,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = repository_description_sync.sync_pair(registry_pair(), dry_run=True)

        self.assertEqual(result, 0)
        self.assertIn("PLAN owner/repo: description", output.getvalue())
        self.assertIn("0 applied", output.getvalue())
        self.assertEqual(gh_api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
