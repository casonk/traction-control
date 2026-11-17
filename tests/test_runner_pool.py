"""Tests for scripts/runner_pool.py (offline: a fake gh stands in for GitHub)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import runner_pool as pool  # noqa: E402


class Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeGh:
    """Holds CI_RUNS_ON per repository and records changes, like gh would."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> Result:
        self.calls.append(list(command))
        if command[:2] == ["gh", "api"]:
            slug = command[2].split("/")[1] + "/" + command[2].split("/")[2]
            if slug in self.values:
                return Result(stdout=self.values[slug] + "\n")
            return Result(1, stderr="gh: Not Found (HTTP 404)")
        slug = command[command.index("--repo") + 1]
        if command[:3] == ["gh", "variable", "set"]:
            self.values[slug] = command[command.index("--body") + 1]
            return Result()
        if command[:3] == ["gh", "variable", "delete"]:
            if slug not in self.values:
                return Result(1, stderr="HTTP 404: Not Found")
            del self.values[slug]
            return Result()
        return Result(1, stderr="unexpected")


SLUGS = ["o/alpha", "o/beta_two", "o/gamma.x", "o/delta"]


class NamingTests(unittest.TestCase):
    def test_scale_set_names(self) -> None:
        self.assertEqual(pool.scale_set_name("local", "o/alpha"), "alpha-ci")
        self.assertEqual(pool.scale_set_name("azure", "o/alpha"), "alpha-azure")
        self.assertEqual(pool.scale_set_name("azure", "o/beta_two"), "beta-two-azure")
        self.assertEqual(pool.scale_set_name("local", "o/gamma.x"), "gamma-x-ci")

    def test_pool_of(self) -> None:
        self.assertEqual(pool.pool_of("o/alpha", None), "hosted")
        self.assertEqual(pool.pool_of("o/alpha", "alpha-ci"), "local")
        self.assertEqual(pool.pool_of("o/alpha", "alpha-azure"), "azure")
        self.assertEqual(pool.pool_of("o/alpha", "something-else"), "other")

    def test_select(self) -> None:
        self.assertEqual(pool.select(SLUGS, []), SLUGS)
        self.assertEqual(pool.select(SLUGS, ["ALPHA", "o/delta"]), ["o/alpha", "o/delta"])
        with self.assertRaises(pool.PoolError):
            pool.select(SLUGS, ["a-public-repo"])


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_azure_refused_without_recorded_scale_sets(self) -> None:
        with self.assertRaises(pool.PoolError) as caught:
            pool.plan_set(SLUGS, "azure", self.state)
        self.assertIn("alpha", str(caught.exception))
        self.assertIn("--force", str(caught.exception))

    def test_azure_allowed_once_enrolled_or_forced(self) -> None:
        pool.save_enrolled(self.state, "azure", set(SLUGS))
        self.assertEqual(
            pool.plan_set(["o/alpha"], "azure", self.state), [("o/alpha", "alpha-azure")]
        )
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(
                pool.plan_set(["o/alpha"], "azure", Path(empty), force=True), [("o/alpha", "alpha-azure")]
            )

    def test_local_and_hosted_need_no_record(self) -> None:
        self.assertEqual(pool.plan_set(["o/alpha"], "local", self.state), [("o/alpha", "alpha-ci")])
        self.assertEqual(pool.plan_set(["o/alpha"], "hosted", self.state), [("o/alpha", None)])

    def test_enrolled_file_is_owner_only(self) -> None:
        pool.save_enrolled(self.state, "azure", {"o/alpha"})
        self.assertEqual(pool.enrolled_file(self.state, "azure").stat().st_mode & 0o777, 0o600)

    def test_weights(self) -> None:
        self.assertEqual(pool.parse_weights("azure=2,local=1"), {"azure": 2, "local": 1})
        for bad in ("azure", "azure=x", "nowhere=1", "azure=0"):
            with self.assertRaises(pool.PoolError):
                pool.parse_weights(bad)

    def test_assign_is_proportional_and_stable(self) -> None:
        many = [f"o/repo{i}" for i in range(12)]
        first = pool.assign(many, {"azure": 2, "local": 1})
        self.assertEqual(first, pool.assign(list(reversed(many)), {"azure": 2, "local": 1}))
        counts = {p: list(first.values()).count(p) for p in ("azure", "local")}
        self.assertEqual(counts, {"azure": 8, "local": 4})


class RegistryFixture(unittest.TestCase):
    """Writes a tiny registry so main() can run without the real one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.private = self.tmp / "private.json"
        self.public = self.tmp / "public.json"
        self.state = self.tmp / "state"
        # Stand in for the registry loader: the real one validates a signed schema.
        self._orig = pool.private_slugs
        pool.private_slugs = lambda *a, **k: list(SLUGS)  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(pool, "private_slugs", self._orig))

    def run_cli(self, *argv: str, gh: FakeGh) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pool.main(
                ["--private", str(self.private), "--public", str(self.public), "--state-dir", str(self.state), *argv],
                runner=gh,
            )
        return code, out.getvalue(), err.getvalue()


class CliTests(RegistryFixture):
    def test_status(self) -> None:
        gh = FakeGh({"o/alpha": "alpha-ci", "o/beta_two": "beta-two-azure"})
        code, out, _ = self.run_cli("status", gh=gh)
        self.assertEqual(code, 0)
        self.assertIn("pools: azure=1, hosted=2, local=1", out)

    def test_status_json(self) -> None:
        code, out, _ = self.run_cli("status", "--json", "--repo", "alpha", gh=FakeGh({"o/alpha": "alpha-ci"}))
        self.assertEqual(json.loads(out), [{"repository": "o/alpha", "pool": "local", "value": "alpha-ci"}])

    def test_set_local_then_hosted(self) -> None:
        gh = FakeGh()
        self.assertEqual(self.run_cli("set", "local", "--repo", "alpha", gh=gh)[0], 0)
        self.assertEqual(gh.values, {"o/alpha": "alpha-ci"})
        self.assertEqual(self.run_cli("set", "hosted", "--repo", "alpha", gh=gh)[0], 0)
        self.assertEqual(gh.values, {})

    def test_hosted_on_an_unset_repo_is_not_an_error(self) -> None:
        self.assertEqual(self.run_cli("set", "hosted", "--repo", "alpha", gh=FakeGh())[0], 0)

    def test_dry_run_changes_nothing(self) -> None:
        gh = FakeGh({"o/alpha": "alpha-ci"})
        code, out, _ = self.run_cli("set", "hosted", "--dry-run", gh=gh)
        self.assertEqual(code, 0)
        self.assertIn("would set alpha -> (hosted: variable removed)", out)
        self.assertEqual(gh.values, {"o/alpha": "alpha-ci"})
        self.assertFalse([c for c in gh.calls if c[:2] == ["gh", "variable"]])

    def test_set_azure_refused_until_enrolled(self) -> None:
        gh = FakeGh()
        code, _, err = self.run_cli("set", "azure", gh=gh)
        self.assertEqual(code, 2)
        self.assertIn("no recorded Azure scale set", err)
        self.assertEqual(gh.values, {})
        self.run_cli("enroll", "azure", gh=gh)
        self.assertEqual(self.run_cli("set", "azure", gh=gh)[0], 0)
        self.assertEqual(gh.values["o/delta"], "delta-azure")

    def test_unenroll(self) -> None:
        gh = FakeGh()
        self.run_cli("enroll", "azure", gh=gh)
        self.run_cli("unenroll", "azure", "--repo", "alpha", gh=gh)
        self.assertNotIn("o/alpha", pool.load_enrolled(self.state, "azure"))
        self.assertIn("o/beta_two", pool.load_enrolled(self.state, "azure"))

    def test_balance_splits_across_pools(self) -> None:
        gh = FakeGh()
        self.run_cli("enroll", "azure", gh=gh)
        code, _, _ = self.run_cli("balance", "--weights", "azure=1,local=1", gh=gh)
        self.assertEqual(code, 0)
        pools = sorted(pool.pool_of(s, gh.values[s]) for s in SLUGS)
        self.assertEqual(pools, ["azure", "azure", "local", "local"])

    def test_unknown_repository_is_an_error(self) -> None:
        code, _, err = self.run_cli("set", "local", "--repo", "a-public-repo", gh=FakeGh())
        self.assertEqual(code, 2)
        self.assertIn("not a private repository", err)

    def test_a_failed_write_is_reported_and_the_rest_continue(self) -> None:
        class Flaky(FakeGh):
            def __call__(self, command: list[str]) -> Result:
                if command[:3] == ["gh", "variable", "set"] and "o/beta_two" in command:
                    return Result(1, stderr="boom")
                return super().__call__(command)

        gh = Flaky()
        code, _, err = self.run_cli("set", "local", gh=gh)
        self.assertEqual(code, 1)
        self.assertIn("beta_two", err)
        self.assertEqual(sorted(gh.values), ["o/alpha", "o/delta", "o/gamma.x"])

    def test_private_lists_slugs(self) -> None:
        _, out, _ = self.run_cli("private", gh=FakeGh())
        self.assertEqual(out.split(), SLUGS)


if __name__ == "__main__":
    unittest.main()
