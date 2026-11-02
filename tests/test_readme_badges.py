"""Tests for readme_badges: the right badges, in the right place, stable under repetition."""

from __future__ import annotations

import base64
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import actions_switch as sw  # noqa: E402
import codecov_secrets as cs  # noqa: E402
import readme_badges as rb  # noqa: E402

TOKEN = "gggggggg-hhhh-iiii-jjjj-kkkkkkkkkkkk"
FULL = rb.Facts("o/alpha", "main", False, "MIT", "LICENSE", True, (3, 10), True)
README = "# alpha\n\nA tagline.\n\n## Usage\n\nRun it.\n"


def b64(text):
    return {"content": base64.b64encode(text.encode()).decode()}


class FakeGitHub:
    """Answers the few repository calls gather() makes."""

    def __init__(self, repos):
        self.repos = repos  # slug -> dict(private, license, paths, pyproject, ci, readme)

    def api(self, method, path, fields=None):
        assert method == "GET"
        parts = path.split("?")[0].split("/")
        slug = "/".join(parts[1:3])
        repo = self.repos.get(slug)
        if repo is None:
            raise sw.GitHubError(f"GET {path}: HTTP 404")
        rest = "/".join(parts[3:])
        if rest == "":
            return {"default_branch": "main", "private": repo.get("private", False), "archived": repo.get("archived", False),
                    "license": {"spdx_id": repo["license"]} if repo.get("license") else None}
        if rest.startswith("git/trees/"):
            return {"tree": [{"path": p} for p in repo.get("paths", [])]}
        if rest == "contents/pyproject.toml" and repo.get("pyproject") is not None:
            return b64(repo["pyproject"])
        if rest == "contents/.github/workflows/ci.yml" and repo.get("ci") is not None:
            return b64(repo["ci"])
        if rest == "contents/README.md" and repo.get("readme") is not None:
            return b64(repo["readme"])
        raise sw.GitHubError(f"GET {path}: HTTP 404")


class PieceTests(unittest.TestCase):
    def test_python_floor_reads_the_lower_bound_only(self):
        for text, want in ((">=3.10", (3, 10)), (">=3.9,<4", (3, 9)), (">= 3.11", (3, 11)),
                           ("~=3.10", None), ("", None), (None, None), ("<4", None)):
            with self.subTest(text=text):
                self.assertEqual(rb.python_floor(text), want)

    def test_static_text_doubles_dashes_and_underscores(self):
        self.assertEqual(rb.static_text("Apache-2.0"), "Apache--2.0")
        self.assertEqual(rb.static_text("LGPL-2.1"), "LGPL--2.1")
        self.assertEqual(rb.static_text("MIT"), "MIT")
        self.assertEqual(rb.static_text("a_b c"), "a__b_c")

    def test_a_full_set_is_ci_license_python_coverage_in_that_order(self):
        items = rb.badges(FULL)
        self.assertEqual([i.split("]")[0] for i in items],
                         ["[![CI", "[![License: MIT", "[![Python 3.10+", "[![Coverage"])
        self.assertIn("actions/workflows/ci.yml/badge.svg?branch=main", items[0])
        self.assertIn("(LICENSE)", items[1])
        self.assertIn("python-3.10%2B-blue", items[2])
        self.assertIn("https://codecov.io/gh/o/alpha/graph/badge.svg)", items[3])

    def test_each_badge_needs_its_own_fact(self):
        self.assertFalse(any("CI" in b for b in rb.badges(rb.Facts("o/a", "main", False, "MIT", ci=False))))
        for unknown in (None, "", "NOASSERTION"):
            self.assertFalse(any("License" in b for b in rb.badges(rb.Facts("o/a", "main", False, unknown, ci=True))))
        self.assertFalse(any("Python" in b for b in rb.badges(rb.Facts("o/a", "main", False, python_floor=None))))
        self.assertFalse(any("Coverage" in b for b in rb.badges(rb.Facts("o/a", "main", False, coverage=False))))
        self.assertEqual(rb.block(rb.Facts("o/a", "main", False)), "")

    def test_the_license_badge_links_to_the_repositorys_own_license_file(self):
        facts = rb.Facts("o/a", "main", False, "Apache-2.0", "LICENSE.md")
        self.assertIn("](LICENSE.md)", rb.badges(facts)[0])
        self.assertIn("license-Apache--2.0-blue", rb.badges(facts)[0])

    def test_a_private_coverage_badge_carries_the_graph_token_and_a_public_one_does_not(self):
        private = rb.Facts("o/p", "main", True, coverage=True)
        self.assertIn(f"badge.svg?token={TOKEN}", rb.badges(private, TOKEN)[0])
        self.assertIn(f"?token={rb.GRAPH_TOKEN_MASK}", rb.badges(private)[0])
        self.assertNotIn("token=", rb.badges(rb.Facts("o/q", "main", False, coverage=True), TOKEN)[0])


class ApplyTests(unittest.TestCase):
    def test_the_block_goes_directly_under_the_title(self):
        out = rb.apply_block(README, rb.block(FULL))
        lines = out.splitlines()
        self.assertEqual(lines[0], "# alpha")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2].startswith("<!-- badges:start"))
        self.assertIn("A tagline.", out)
        self.assertTrue(out.index("<!-- badges:end -->") < out.index("A tagline."))
        self.assertIn("## Usage\n\nRun it.\n", out)

    def test_applying_twice_changes_nothing(self):
        once = rb.apply_block(README, rb.block(FULL))
        self.assertEqual(rb.apply_block(once, rb.block(FULL)), once)

    def test_a_changed_fact_replaces_the_block_in_place(self):
        once = rb.apply_block(README, rb.block(FULL))
        changed = rb.apply_block(once, rb.block(rb.Facts("o/alpha", "main", False, "Apache-2.0", "LICENSE", True, (3, 11), False)))
        self.assertEqual(changed.count("<!-- badges:start"), 1)
        self.assertIn("Apache--2.0", changed)
        self.assertNotIn("Coverage", changed)
        self.assertIn("A tagline.", changed)

    def test_a_block_someone_moved_stays_where_they_put_it(self):
        moved = "# alpha\n\nA tagline.\n\n" + rb.block(FULL) + "\n## Usage\n\nRun it.\n"
        changed = rb.apply_block(moved, rb.block(rb.Facts("o/alpha", "main", False, "Apache-2.0", "LICENSE", True, (3, 11), False)))
        self.assertEqual(changed.count("<!-- badges:start"), 1)
        self.assertLess(changed.index("A tagline."), changed.index("<!-- badges:start"))
        self.assertIn("Apache--2.0", changed)
        self.assertEqual(rb.apply_block(changed, rb.block(rb.Facts("o/alpha", "main", False, "Apache-2.0", "LICENSE", True, (3, 11), False))), changed)

    def test_an_empty_block_removes_an_old_one_and_leaves_the_text(self):
        once = rb.apply_block(README, rb.block(FULL))
        self.assertEqual(rb.apply_block(once, ""), README)

    def test_text_without_a_block_is_untouched_by_an_empty_block(self):
        self.assertEqual(rb.apply_block(README, ""), README)

    def test_a_readme_without_a_title_is_refused_not_mangled(self):
        with self.assertRaises(rb.BadgeError):
            rb.apply_block("Just text.\n", rb.block(FULL))
        with self.assertRaises(rb.BadgeError):
            rb.apply_block("", rb.block(FULL))

    def test_a_title_followed_directly_by_a_section_keeps_its_spacing(self):
        out = rb.apply_block("# t\n## s\ntext\n", rb.block(FULL))
        self.assertIn("<!-- badges:end -->\n\n## s\ntext\n", out)

    def test_hand_made_badges_outside_the_block_are_reported_but_not_removed(self):
        text = "# t\n\n[![PyPI](https://img.shields.io/pypi/v/x)](https://pypi.org/project/x/)\n\nBody\n"
        out = rb.apply_block(text, rb.block(FULL))
        self.assertIn("img.shields.io/pypi/v/x", out)
        self.assertEqual(len(rb.hand_made_badges(out)), 1)  # the block's own badges are not counted


class JudgeTests(unittest.TestCase):
    def test_each_state(self):
        good = rb.apply_block(README, rb.block(FULL))
        self.assertEqual(rb.judge(FULL, good)[0], "ok")
        self.assertEqual(rb.judge(FULL, README)[0], "missing")
        self.assertEqual(rb.judge(FULL, good.replace("python-3.10", "python-3.9"))[0], "drifted")
        self.assertEqual(rb.judge(rb.Facts("o/a", "main", False), good)[0], "stale")
        self.assertEqual(rb.judge(rb.Facts("o/a", "main", False), README)[0], "ok")
        self.assertEqual(rb.judge(FULL, None)[0], "no-readme")

    def test_an_archived_repository_is_skipped_whatever_its_readme_says(self):
        archived = rb.Facts("o/old", "main", False, "MIT", ci=True, archived=True)
        self.assertEqual(rb.judge(archived, README)[0], "archived")
        self.assertEqual(rb.judge(archived, None)[0], "archived")

    def test_a_private_repositorys_real_token_is_not_content_drift(self):
        private = rb.Facts("o/p", "main", True, coverage=True)
        with_token = rb.apply_block("# p\n", rb.block(private, TOKEN))
        self.assertEqual(rb.judge(private, with_token)[0], "ok")
        # ...but a different badge URL still is
        self.assertEqual(rb.judge(private, with_token.replace("codecov.io/gh/o/p", "codecov.io/gh/o/x"))[0], "drifted")


class GatherTests(unittest.TestCase):
    def repo(self, **over):
        base = {"license": "MIT", "paths": [".github/workflows/ci.yml", "LICENSE", "pyproject.toml", "README.md"],
                "pyproject": '[project]\nrequires-python = ">=3.10"\n',
                "ci": "jobs:\n  ci:\n    with:\n      coverage: true\n", "readme": README}
        base.update(over)
        return base

    def test_facts_come_from_the_default_branch(self):
        facts, readme = rb.gather("o/alpha", FakeGitHub({"o/alpha": self.repo()}))
        self.assertEqual(facts, rb.Facts("o/alpha", "main", False, "MIT", "LICENSE", True, (3, 10), True))
        self.assertEqual(readme, README)

    def test_missing_pieces_simply_mean_no_badge(self):
        repo = self.repo(paths=["README.md"], pyproject=None, ci=None, license=None)
        facts, _ = rb.gather("o/alpha", FakeGitHub({"o/alpha": repo}))
        self.assertEqual(rb.badges(facts), [])

    def test_coverage_is_read_from_the_workflow_not_guessed(self):
        for text, want in (("coverage: true", True), ("coverage: false", False), ("# coverage: true", False), ("coverage-source: x", False)):
            with self.subTest(text=text):
                facts, _ = rb.gather("o/alpha", FakeGitHub({"o/alpha": self.repo(ci=text + "\n")}))
                self.assertEqual(facts.coverage, want)

    def test_check_reports_each_repository_and_survives_one_failing(self):
        github = FakeGitHub({"o/alpha": self.repo(readme=rb.apply_block(README, rb.block(FULL))), "o/beta": self.repo()})
        results = dict((s, st) for s, st, _ in rb.check(["o/alpha", "o/beta", "o/gone"], github))
        self.assertEqual(results, {"o/alpha": "ok", "o/beta": "missing", "o/gone": "error"})


class GraphTokenTests(unittest.TestCase):
    def run_with(self, pat=(0, "p" * 36 + "\n", ""), http=None):
        def runner(command, stdin):
            return subprocess.CompletedProcess([], pat[0], pat[1], pat[2])

        return lambda: rb.graph_token("o/p", runner, http or (lambda url, bearer: (200, {"graph_token": TOKEN, "upload_token": "u" * 36})))

    def test_it_returns_the_graph_token_and_never_the_upload_token(self):
        self.assertEqual(self.run_with()(), TOKEN)

    def test_a_short_graph_token_is_accepted_but_one_that_could_break_markdown_is_not(self):
        short = lambda url, bearer: (200, {"graph_token": "aB3_x-9.Zq"})  # noqa: E731 - 10 characters
        self.assertEqual(self.run_with(http=short)(), "aB3_x-9.Zq")
        for unsafe in ("has space", "a)b](http://evil", "x" * 200, "short", "", None):
            with self.subTest(token=unsafe):
                http = lambda url, bearer, t=unsafe: (200, {"graph_token": t})  # noqa: E731
                with self.assertRaisesRegex(rb.BadgeError, "no usable graph token"):
                    self.run_with(http=http)()

    def test_no_pat_or_no_token_is_a_clear_error(self):
        with self.assertRaisesRegex(rb.BadgeError, "cannot read the Codecov PAT"):
            self.run_with(pat=(1, "", "daemon is locked"))()
        with self.assertRaisesRegex(rb.BadgeError, "install the Codecov GitHub app"):
            self.run_with(http=lambda url, bearer: (404, None))()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub({
            "casonk/alpha": GatherTests.repo(None, readme=README),
            "casonk/beta": GatherTests.repo(None, readme=rb.apply_block(README, rb.block(rb.Facts("casonk/beta", "main", False, "MIT", "LICENSE", True, (3, 10), True)))),
        })

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rb.main(list(argv), github=self.github)
        return code, out.getvalue(), err.getvalue()

    def test_check_lists_only_what_needs_attention_and_sets_the_exit_status(self):
        code, out, _ = self.run_cli("check", "--repo", "alpha", "--repo", "beta")
        self.assertEqual(code, 1)
        self.assertIn("casonk/alpha: missing", out)
        self.assertNotIn("casonk/beta:", out)
        self.assertIn("2 repositories checked, 1 need attention", out)
        code, out, _ = self.run_cli("check", "--repo", "beta")
        self.assertEqual((code, out.strip()), (0, "1 repositories checked, 0 need attention"))

    def test_check_does_not_count_archived_repositories(self):
        self.github.repos["casonk/old"] = GatherTests.repo(None, archived=True, readme=None)
        code, out, _ = self.run_cli("check", "--repo", "old", "--repo", "beta")
        self.assertEqual((code, out.strip()), (0, "2 repositories checked, 0 need attention"))

    def test_check_needs_a_selection(self):
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 2)
        self.assertIn("--all or at least one --repo", err)

    def test_render_prints_the_block(self):
        code, out, _ = self.run_cli("render", "--repo", "alpha")
        self.assertEqual(code, 0)
        self.assertIn("<!-- badges:start", out)

    def test_apply_writes_the_file_and_dry_run_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text(README)
            code, out, _ = self.run_cli("apply", "--repo", "alpha", "--readme", str(path), "--dry-run")
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(), README)
            self.assertIn("<!-- badges:start", out)
            code, _, err = self.run_cli("apply", "--repo", "alpha", "--readme", str(path))
            self.assertEqual(code, 0)
            self.assertIn("alpha: updated", err)
            self.assertIn("<!-- badges:start", path.read_text())
            code, _, err = self.run_cli("apply", "--repo", "alpha", "--readme", str(path))
            self.assertIn("alpha: unchanged", err)

    def test_apply_refuses_a_readme_without_a_title_and_leaves_it_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text("no title here\n")
            code, _, err = self.run_cli("apply", "--repo", "alpha", "--readme", str(path))
            self.assertEqual(code, 2)
            self.assertIn("does not start with", err)
            self.assertEqual(path.read_text(), "no title here\n")


if __name__ == "__main__":
    unittest.main()
