"""Tests for the local, privacy-safe portfolio backlog index."""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import portfolio_materializer as materializer  # noqa: E402
import render_portfolio_backlog as backlog_index  # noqa: E402
import repository_visibility as visibility  # noqa: E402


class PortfolioBacklogIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.private = self.root / "private.local.json"
        self.public = self.root / "public.local.json"
        self.catalog_path = self.root / "portfolio.local.json"
        self._write_json(self.private, self._registry("private", [{"id": "R_PRIVATE", "slug": "owner/private-tool"}]))
        self._write_json(self.public, self._registry("public", [{"id": "R_PUBLIC", "slug": "owner/public-tool"}]))
        self.pair = visibility.load_pair(self.private, self.public)
        self._write_json(
            self.catalog_path,
            {
                "schema_version": 1,
                "registry_id": "portfolio-test",
                "registry_generation": 1,
                "catalog_generation": 1,
                "repositories": [
                    {"repository_id": "R_PRIVATE", "relative_path": "private", "lifecycle": "active", "sync_policy": "manual", "desired_presence": "checkout"},
                    {"repository_id": "R_PUBLIC", "relative_path": "public", "lifecycle": "active", "sync_policy": "manual", "desired_presence": "checkout"},
                ],
            },
        )
        self.catalog = materializer.load_catalog(self.catalog_path, self.pair)

    @staticmethod
    def _registry(kind: str, repositories: list[dict[str, str]]) -> dict[str, object]:
        return {"schema_version": 1, "registry_id": "portfolio-test", "generation": 1, "visibility": kind, "repositories": repositories}

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def test_report_has_metadata_but_never_canonical_backlog_text(self) -> None:
        (self.root / "private").mkdir()
        (self.root / "public").mkdir()
        (self.root / "private/BACKLOG.md").write_text(
            "## Pending\n- [ ] P1 private-router hostname and secret details\n", encoding="utf-8"
        )
        (self.root / "public/BACKLOG.md").write_text(
            "## In Progress\n- [ ] P2 make the public adapter durable\n", encoding="utf-8"
        )

        items, unavailable = backlog_index.collect_items(self.pair, self.catalog, self.root)
        report = backlog_index.render_markdown(items, unavailable)

        self.assertEqual(unavailable, 0)
        self.assertIn("owner/public-tool", report)
        self.assertIn("R_PRIVATE", report)
        self.assertIn("P1", report)
        self.assertIn("in-progress", report)
        self.assertNotIn("private-router", report)
        self.assertNotIn("secret details", report)
        self.assertNotIn("make the public adapter durable", report)

    def test_ignored_title_map_can_supply_a_reviewed_safe_summary(self) -> None:
        (self.root / "public").mkdir()
        source = "P1 private implementation wording"
        (self.root / "public/BACKLOG.md").write_text(f"## Pending\n- [ ] {source}\n", encoding="utf-8")
        item_id = backlog_index._item_id("R_PUBLIC", source)
        title_map = self.root / "titles.local.json"
        self._write_json(title_map, {"schema_version": 1, "titles": {item_id: "Durable delivery"}})

        items, _ = backlog_index.collect_items(
            self.pair, self.catalog, self.root, titles=backlog_index._load_titles(title_map)
        )

        self.assertEqual(items[0].title, "Durable delivery")

    def test_output_is_replaced_as_owner_only_file(self) -> None:
        output = self.root / "reports/index.md"
        backlog_index._write_owner_only(output, "# report\n")
        self.assertEqual(output.read_text(encoding="utf-8"), "# report\n")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
