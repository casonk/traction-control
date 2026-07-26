"""Real-Git regression tests for portfolio materialization and lifecycle review."""

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


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import portfolio_lifecycle_review as lifecycle  # noqa: E402
import portfolio_materializer as materializer  # noqa: E402
import repository_visibility as visibility  # noqa: E402


class PortfolioMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.portfolio = self.root / "portfolio"
        self.remotes = self.root / "remotes"
        self.seeds = self.root / "seeds"
        self.registry = self.root / "registry"
        self.portfolio.mkdir()
        self.remotes.mkdir()
        self.seeds.mkdir()
        self.registry.mkdir()
        self.private_path = self.registry / "private.local.json"
        self.public_path = self.registry / "public.local.json"
        self.catalog_path = self.registry / "portfolio.local.json"
        self._write_pair()
        self._write_catalog()
        self.pair = visibility.load_pair(self.private_path, self.public_path)
        self.catalog = materializer.load_catalog(self.catalog_path, self.pair)
        self._create_remote("example-owner/alpha")
        self._create_remote("example-owner/beta")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(
        self,
        *command: str,
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            env=env,
        )

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _write_pair(self) -> None:
        common = {
            "schema_version": 1,
            "registry_id": "materializer-test",
            "generation": 4,
        }
        self._write_json(
            self.private_path,
            {
                **common,
                "visibility": "private",
                "repositories": [
                    {
                        "id": "R_alpha",
                        "slug": "example-owner/alpha",
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
                        "id": "R_beta",
                        "slug": "example-owner/beta",
                    }
                ],
            },
        )

    def _catalog_payload(
        self,
        *,
        alpha_path: str = "repos/alpha",
        beta_path: str = "repos/beta",
        alpha_presence: str = "checkout",
        beta_presence: str = "checkout",
        alpha_policy: str = "fetch-only",
        beta_policy: str = "fetch-only",
    ) -> dict[str, object]:
        rows = [
            {
                "repository_id": "R_alpha",
                "relative_path": alpha_path,
                "lifecycle": "active",
                "sync_policy": alpha_policy,
                "desired_presence": alpha_presence,
            },
            {
                "repository_id": "R_beta",
                "relative_path": beta_path,
                "lifecycle": "active",
                "sync_policy": beta_policy,
                "desired_presence": beta_presence,
            },
        ]
        rows.sort(
            key=lambda row: (
                str(row["relative_path"]).casefold(),
                str(row["repository_id"]),
            )
        )
        return {
            "schema_version": 1,
            "registry_id": "materializer-test",
            "registry_generation": 4,
            "catalog_generation": 2,
            "repositories": rows,
        }

    def _write_catalog(self, **overrides: object) -> None:
        self._write_json(
            self.catalog_path,
            self._catalog_payload(**overrides),
        )

    def _create_remote(self, slug: str) -> Path:
        _, name = slug.split("/", 1)
        seed = self.seeds / name
        bare = self.remotes / f"{slug}.git"
        self._run("git", "init", "-q", "-b", "main", str(seed))
        self._run("git", "-C", str(seed), "config", "user.name", "Test User")
        self._run(
            "git",
            "-C",
            str(seed),
            "config",
            "user.email",
            "test@example.invalid",
        )
        (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        self._run("git", "-C", str(seed), "add", "README.md")
        self._run("git", "-C", str(seed), "commit", "-qm", "initial")
        bare.parent.mkdir(parents=True, exist_ok=True)
        self._run("git", "clone", "-q", "--bare", str(seed), str(bare))
        self._run("git", "-C", str(seed), "remote", "add", "origin", str(bare))
        return bare

    def _push_seed_commit(self, name: str, filename: str) -> str:
        seed = self.seeds / name
        (seed / filename).write_text(f"{filename}\n", encoding="utf-8")
        self._run("git", "-C", str(seed), "add", filename)
        self._run("git", "-C", str(seed), "commit", "-qm", f"add {filename}")
        self._run("git", "-C", str(seed), "push", "-q", "origin", "main")
        return self._run(
            "git",
            "-C",
            str(seed),
            "rev-parse",
            "HEAD",
        ).stdout.strip()

    def _rewrite_environment(self) -> dict[str, str]:
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
        return environment

    def _controlled_product_run(
        self,
        command: list[str] | tuple[str, ...],
        *,
        timeout: float = 300.0,
        commands: list[tuple[str, ...]] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if commands is not None:
            commands.append(tuple(command))
        verb_index = 1
        if len(command) > 2 and command[0] == "git" and command[1] == "-C":
            verb_index = 3
        use_rewrite = (
            command
            and command[0] == "git"
            and len(command) > verb_index
            and command[verb_index] in {"clone", "fetch"}
        )
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self._rewrite_environment() if use_rewrite else os.environ.copy(),
        )

    def _materialize(
        self,
        commands: list[tuple[str, ...]] | None = None,
    ) -> None:
        def controlled_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._controlled_product_run(
                command,
                timeout=timeout,
                commands=commands,
            )

        with mock.patch.object(materializer, "_run", side_effect=controlled_run):
            materializer.materialize(
                self.pair,
                self.catalog,
                self.portfolio,
                clone_protocol="https",
                gh_command="unused",
                skip_github=True,
            )

    def test_catalog_rejects_coverage_and_path_conflicts(self) -> None:
        payload = self._catalog_payload()
        payload["repositories"] = payload["repositories"][:-1]
        self._write_json(self.catalog_path, payload)
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "coverage mismatch",
        ):
            materializer.load_catalog(self.catalog_path, self.pair)

        self._write_catalog(alpha_path="repos", beta_path="repos/beta")
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "may not contain one another",
        ):
            materializer.load_catalog(self.catalog_path, self.pair)

        self._write_catalog(alpha_path="../escape")
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "normalized and relative|unsafe",
        ):
            materializer.load_catalog(self.catalog_path, self.pair)

    def test_real_clone_is_atomic_idempotent_and_auditable(self) -> None:
        commands: list[tuple[str, ...]] = []
        self._materialize(commands)
        self._materialize(commands)
        materializer.audit_catalog(
            self.pair,
            self.catalog,
            self.portfolio,
        )

        for name in ("alpha", "beta"):
            checkout = self.portfolio / "repos" / name
            self.assertTrue((checkout / ".git").is_dir())
            self.assertEqual(
                self._run(
                    "git",
                    "-C",
                    str(checkout),
                    "remote",
                    "get-url",
                    "origin",
                ).stdout.strip(),
                f"https://github.com/example-owner/{name}.git",
            )
        clone_commands = [
            command
            for command in commands
            if len(command) >= 2 and command[0] == "git" and command[1] == "clone"
        ]
        self.assertEqual(len(clone_commands), 2)

        allowed_git_verbs = {
            "clone",
            "config",
            "fetch",
            "remote",
            "rev-parse",
            "status",
        }
        for command in commands:
            if not command or command[0] != "git":
                continue
            verb_index = 1
            if len(command) > 2 and command[1] == "-C":
                verb_index = 3
            self.assertIn(command[verb_index], allowed_git_verbs)
            self.assertNotEqual(command[verb_index], "push")
            if command[verb_index] == "remote":
                self.assertNotIn("set-url", command)

    def test_fetch_only_sync_updates_remote_ref_but_not_worktree_head(self) -> None:
        self._materialize()
        checkout = self.portfolio / "repos" / "alpha"
        before = self._run(
            "git",
            "-C",
            str(checkout),
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        remote_head = self._push_seed_commit("alpha", "new.txt")

        def controlled_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._controlled_product_run(command, timeout=timeout)

        with mock.patch.object(materializer, "_run", side_effect=controlled_run):
            materializer.synchronize(self.pair, self.catalog, self.portfolio)

        after = self._run(
            "git",
            "-C",
            str(checkout),
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        observed_remote = self._run(
            "git",
            "-C",
            str(checkout),
            "rev-parse",
            "origin/main",
        ).stdout.strip()
        self.assertEqual(after, before)
        self.assertEqual(observed_remote, remote_head)

    def test_dirty_checkout_refuses_fetch_without_updating_remote_ref(self) -> None:
        self._materialize()
        checkout = self.portfolio / "repos" / "alpha"
        before_remote = self._run(
            "git",
            "-C",
            str(checkout),
            "rev-parse",
            "origin/main",
        ).stdout.strip()
        remote_head = self._push_seed_commit("alpha", "remote.txt")
        self.assertNotEqual(before_remote, remote_head)
        (checkout / "untracked.txt").write_text("local\n", encoding="utf-8")

        def controlled_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._controlled_product_run(command, timeout=timeout)

        with mock.patch.object(materializer, "_run", side_effect=controlled_run):
            with self.assertRaisesRegex(
                materializer.MaterializerError,
                "dirty/staged/untracked",
            ):
                materializer.synchronize(self.pair, self.catalog, self.portfolio)

        self.assertEqual(
            self._run(
                "git",
                "-C",
                str(checkout),
                "rev-parse",
                "origin/main",
            ).stdout.strip(),
            before_remote,
        )
        self.assertTrue((checkout / "untracked.txt").is_file())

    def test_wrong_or_mixed_registered_remote_identity_fails(self) -> None:
        self._materialize()
        checkout = self.portfolio / "repos" / "alpha"
        self._run(
            "git",
            "-C",
            str(checkout),
            "remote",
            "set-url",
            "origin",
            "https://github.com/example-owner/beta.git",
        )
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "identity mismatch",
        ):
            materializer.audit_catalog(self.pair, self.catalog, self.portfolio)

        self._run(
            "git",
            "-C",
            str(checkout),
            "remote",
            "set-url",
            "origin",
            "https://github.com/example-owner/alpha.git",
        )
        self._run(
            "git",
            "-C",
            str(checkout),
            "remote",
            "set-url",
            "--add",
            "--push",
            "origin",
            "git@github.com:example-owner/beta.git",
        )
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "identity mismatch",
        ):
            materializer.audit_catalog(self.pair, self.catalog, self.portfolio)

    def test_nonrepo_and_symlink_collisions_fail_without_replacement(self) -> None:
        collision = self.portfolio / "repos" / "alpha"
        collision.mkdir(parents=True)
        marker = collision / "keep.txt"
        marker.write_text("keep\n", encoding="utf-8")
        with self.assertRaises(materializer.MaterializerError):
            self._materialize()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

        collision.rename(self.root / "saved-collision")
        (self.portfolio / "repos").rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.portfolio / "repos").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "symlink",
        ):
            self._materialize()
        self.assertEqual(tuple(outside.iterdir()), ())

    def test_clone_failure_leaves_no_target_or_temporary_checkout(self) -> None:
        (self.remotes / "example-owner" / "beta.git").rename(
            self.root / "missing-beta.git"
        )
        with self.assertRaisesRegex(
            materializer.MaterializerError,
            "Git clone failed",
        ):
            self._materialize()
        target = self.portfolio / "repos" / "beta"
        self.assertFalse(target.exists())
        self.assertEqual(
            tuple(target.parent.glob(".beta.materialize.*")),
            (),
        )

    def test_desired_absent_and_manual_sync_are_non_mutating(self) -> None:
        self._write_catalog(
            alpha_presence="absent",
            beta_policy="manual",
        )
        self.catalog = materializer.load_catalog(self.catalog_path, self.pair)
        self._materialize()
        self.assertFalse((self.portfolio / "repos" / "alpha").exists())
        beta = self.portfolio / "repos" / "beta"
        before = self._run(
            "git",
            "-C",
            str(beta),
            "rev-parse",
            "origin/main",
        ).stdout.strip()
        self._push_seed_commit("beta", "manual.txt")
        def controlled_run(
            command: list[str] | tuple[str, ...],
            *,
            timeout: float = 300.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._controlled_product_run(command, timeout=timeout)

        with mock.patch.object(materializer, "_run", side_effect=controlled_run):
            materializer.synchronize(self.pair, self.catalog, self.portfolio)
        after = self._run(
            "git",
            "-C",
            str(beta),
            "rev-parse",
            "origin/main",
        ).stdout.strip()
        self.assertEqual(after, before)

    def test_init_preserves_bound_path_and_defaults_missing_repository(self) -> None:
        self._write_catalog(beta_presence="absent")
        self.catalog = materializer.load_catalog(self.catalog_path, self.pair)
        self._materialize()
        initialized_path = self.registry / "initialized.local.json"

        initialized = materializer.initialize_catalog(
            self.pair,
            initialized_path,
            self.portfolio,
        )
        by_id = {
            entry.repository_id: entry.relative_path
            for entry in initialized.repositories
        }
        self.assertEqual(by_id["R_alpha"], "repos/alpha")
        self.assertEqual(
            by_id["R_beta"],
            "github/example-owner/beta",
        )
        self.assertEqual(stat.S_IMODE(initialized_path.stat().st_mode), 0o600)

    def test_refresh_versions_observed_archive_state_without_remote_mutation(self) -> None:
        fake_gh = self.root / "fake-gh"
        fake_gh.write_text(
            "#!/bin/sh\n"
            "case \"$3\" in\n"
            "  example-owner/alpha) archived=true ;;\n"
            "  example-owner/beta) archived=false ;;\n"
            "  *) exit 64 ;;\n"
            "esac\n"
            "id=R_${3##*/}\n"
            "visibility=PUBLIC\n"
            "[ \"$3\" = example-owner/alpha ] && visibility=PRIVATE\n"
            "printf '{\"id\":\"%s\",\"nameWithOwner\":\"%s\","
            "\"visibility\":\"%s\",\"isArchived\":%s}\\n' "
            "\"$id\" \"$3\" \"$visibility\" \"$archived\"\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o700)

        refreshed, changed = materializer.refresh_archive_states(
            self.pair,
            self.catalog,
            gh_command=str(fake_gh),
        )
        self.assertEqual(changed, 1)
        self.assertEqual(refreshed.catalog_generation, 3)
        by_id = {
            entry.repository_id: entry.lifecycle
            for entry in refreshed.repositories
        }
        self.assertEqual(by_id, {"R_alpha": "archived", "R_beta": "active"})

        unchanged, changed = materializer.refresh_archive_states(
            self.pair,
            refreshed,
            gh_command=str(fake_gh),
        )
        self.assertEqual(changed, 0)
        self.assertEqual(unchanged.catalog_generation, 3)

    def test_lifecycle_report_records_dependency_evidence_securely(self) -> None:
        self._materialize()
        alpha = self.portfolio / "repos" / "alpha"
        manifest = alpha / "pyproject.toml"
        manifest.write_text(
            '[project]\nname = "alpha"\n'
            'dependency-source = "example-owner/beta"\n',
            encoding="utf-8",
        )
        self._run("git", "-C", str(alpha), "add", "pyproject.toml")
        self._run(
            "git",
            "-C",
            str(alpha),
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "add dependency evidence",
        )
        scan = lifecycle.scan_portfolio(
            self.pair,
            self.catalog,
            self.portfolio,
        )
        self.assertEqual(scan.blockers, ())
        self.assertTrue(
            any(
                reference["source_repository_id"] == "R_alpha"
                and reference["target_repository_id"] == "R_beta"
                and reference["file"] == "pyproject.toml"
                for reference in scan.references
            )
        )
        report = lifecycle.build_report(
            self.pair,
            self.catalog,
            self.portfolio,
            scan,
            (),
        )
        report_path = self.root / "reports" / "review.local.json"
        lifecycle._write_owner_only_json(report_path, report)
        self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertFalse(payload["safety"]["changes_applied"])

    def test_lifecycle_missing_checkout_and_invalid_plan_fail_closed(self) -> None:
        scan = lifecycle.scan_portfolio(
            self.pair,
            self.catalog,
            self.portfolio,
        )
        self.assertEqual(len(scan.blockers), 2)
        self.assertTrue(
            all(issue["code"] == "missing-checkout" for issue in scan.blockers)
        )

        plan_path = self.registry / "plan.local.json"
        self._write_json(
            plan_path,
            {
                "schema_version": 1,
                "actions": [
                    {
                        "action": "remove-dependency",
                        "target_repository_id": "R_alpha",
                        "dependency_repository_id": "R_alpha",
                        "reason": "Invalid self dependency.",
                    }
                ],
            },
        )
        with self.assertRaisesRegex(lifecycle.ReviewError, "self-dependency"):
            lifecycle.load_plan(plan_path, self.pair)


if __name__ == "__main__":
    unittest.main()
