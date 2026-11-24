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


def remote(
    *,
    description: str = "",
    archived: bool = False,
    topics: tuple[str, ...] = ("tools",),
) -> SimpleNamespace:
    return SimpleNamespace(
        owner="owner",
        name="repo",
        visibility="public",
        archived=archived,
        default_branch="main",
        description=description,
        topics=topics,
        slug="owner/repo",
    )


def encoded_settings(
    description: str = "A useful description.",
    topics: tuple[str, ...] | None = ("tools",),
) -> dict[str, str]:
    lines = ["[repository]", f'description = "{description}"']
    if topics is not None:
        listed = ", ".join(f'"{topic}"' for topic in topics)
        lines.append(f"topics = [{listed}]")
    text = "\n".join(lines) + "\n"
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

    def test_applies_topics_when_they_differ_and_description_is_set(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(description="Already set.", topics=("old",)),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                side_effect=[encoded_settings(topics=("new",)), {}],
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
                    "PUT",
                    "repos/owner/repo/topics",
                    "--input",
                    "-",
                    stdin='{"names": ["new"]}',
                ),
            ],
        )
        text = output.getvalue()
        self.assertIn("APPLY owner/repo: topics", text)
        self.assertNotIn("description", text)
        self.assertIn("1 applied", text)

    def test_missing_topics_are_reported_without_a_put(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(description="Already set."),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                return_value=encoded_settings(topics=None),
            ) as gh_api,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = repository_description_sync.sync_pair(registry_pair())

        self.assertEqual(result, 1)
        self.assertIn("topics are missing", output.getvalue())
        self.assertEqual(gh_api.call_count, 1)

    def test_dry_run_plans_topics_without_putting_them(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(description="Already set.", topics=("old",)),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                return_value=encoded_settings(topics=("new",)),
            ) as gh_api,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = repository_description_sync.sync_pair(registry_pair(), dry_run=True)

        self.assertEqual(result, 0)
        self.assertIn("PLAN owner/repo: topics", output.getvalue())
        self.assertIn("0 applied", output.getvalue())
        self.assertEqual(gh_api.call_count, 1)

    def test_private_topic_drift_does_not_print_the_slug(self) -> None:
        with (
            patch.object(
                repository_description_sync.repo_settings,
                "fetch_remote",
                return_value=remote(description="Already set.", topics=("old",)),
            ),
            patch.object(
                repository_description_sync.repo_settings,
                "gh_api",
                side_effect=[encoded_settings(topics=("new",)), {}],
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            result = repository_description_sync.sync_pair(
                registry_pair(visibility="private", slug="owner/secret-name")
            )

        self.assertEqual(result, 0)
        self.assertNotIn("secret-name", stdout.getvalue())
        self.assertNotIn("secret-name", stderr.getvalue())
        self.assertIn("APPLY private repository: topics", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
