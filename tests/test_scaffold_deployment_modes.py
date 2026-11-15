"""Tests for the deployment-modes scaffold and its conformance checker.

The strongest test is the round trip: scaffold each kind into a fresh repository, run the *generated* tests against
the *generated* scripts, and run the checker on the result. A generated repository must pass both with no edits.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_deployment_modes as checker  # noqa: E402
import scaffold_deployment_modes as scaffold  # noqa: E402

HAS_PYTEST = importlib.util.find_spec("pytest") is not None

# One scaffold per kind, using addresses that are free in docs/deployment-modes.md.
KINDS = {
    "job": ["--app", "demo-job"],
    "triggered": ["--app", "demo-heavy"],
    "worker": ["--app", "demo-worker"],
    "service": ["--app", "demo-web", "--cluster-ip", "10.43.0.91", "--port", "5591"],
    "sandbox": ["--app", "demo-box", "--cluster-ip", "10.43.0.92", "--port", "5592"],
}


def run_scaffold(repo: Path, kind: str, *extra: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scaffold.main(["--repo", str(repo), "--kind", kind, *KINDS[kind], *extra])
    return code, out.getvalue(), err.getvalue()


def make_repo(root: str, name: str = "repo") -> Path:
    repo = Path(root) / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("x = 1\n")
    (repo / "README.md").write_text("# demo\n\nIntro.\n\n## Usage\n\nx\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def checked(repo: Path):
    return checker.check(repo)


def failures(report) -> list[str]:
    return [rule for status, rule, _ in report.results if status == checker.FAIL]


class RoundTripTests(unittest.TestCase):
    def test_each_kind_generates_scripts_with_valid_shell_syntax(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                repo = make_repo(root)
                code, _, err = run_scaffold(repo, kind)
                self.assertEqual(code, 0, err)
                for script in (repo / "deploy").rglob("*.sh"):
                    done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
                    self.assertEqual(done.returncode, 0, f"{script.name}: {done.stderr}")
                    self.assertTrue(os.access(script, os.X_OK), f"{script.name} is not executable")

    @unittest.skipUnless(HAS_PYTEST, "pytest is not installed")
    def test_the_generated_tests_pass_against_the_generated_scripts_with_no_edits(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                repo = make_repo(root)
                self.assertEqual(run_scaffold(repo, kind)[0], 0)
                done = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_deploy_modes.py"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                self.assertEqual(done.returncode, 0, done.stdout[-2000:] + done.stderr[-500:])

    def test_a_generated_repository_passes_the_checker_with_only_stub_warnings(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                repo = make_repo(root)
                self.assertEqual(run_scaffold(repo, kind)[0], 0)
                report = checked(repo)
                self.assertEqual(failures(report), [])
                warnings = {rule for status, rule, _ in report.results if status == checker.WARN}
                self.assertEqual(
                    warnings,
                    {
                        "deploy/k3s/apply.sh is still a scaffold stub",
                        "deploy/k3s/verify.sh is still a scaffold stub",
                    },
                )
                self.assertIn(f"kind: {kind}", [rule for _, rule, _ in report.results])

    def test_the_stubs_fail_loudly_rather_than_pretend_to_work(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            run_scaffold(repo, "job")
            for name in ("apply.sh", "verify.sh"):
                done = subprocess.run(["bash", str(repo / "deploy" / "k3s" / name)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 1)
                self.assertIn("scaffold stub", done.stderr)

    def test_host_data_adds_prepare_host_to_the_steps_and_the_generated_tests(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            self.assertEqual(run_scaffold(repo, "worker", "--host-data")[0], 0)
            self.assertTrue((repo / "deploy" / "k3s" / "prepare-host.sh").exists())
            deploy = (repo / "deploy" / "deploy.sh").read_text()
            self.assertLess(deploy.index("prepare-host.sh"), deploy.index("build-image.sh"))
            self.assertIn('K3S_STEPS = ["prepare-host.sh", "build-image.sh", "apply.sh", "verify.sh"]',
                          (repo / "tests" / "test_deploy_modes.py").read_text())
            self.assertEqual(failures(checked(repo)), [])


class ScaffoldBehaviourTests(unittest.TestCase):
    def test_it_refuses_to_overwrite_and_leaves_everything_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            (repo / "deploy").mkdir()
            existing = repo / "deploy" / "deploy.sh"
            existing.write_text("# mine\n")
            code, _, err = run_scaffold(repo, "job")
            self.assertEqual(code, 2)
            self.assertIn("already exist", err)
            self.assertEqual(existing.read_text(), "# mine\n")
            self.assertFalse((repo / "tests" / "test_deploy_modes.py").exists(), "nothing else was written")

    def test_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            (repo / "deploy").mkdir()
            (repo / "deploy" / "deploy.sh").write_text("# mine\n")
            self.assertEqual(run_scaffold(repo, "job", "--force")[0], 0)
            self.assertIn("deploy.sh", (repo / "deploy" / "deploy.sh").read_text())
            self.assertNotEqual((repo / "deploy" / "deploy.sh").read_text(), "# mine\n")

    def test_dry_run_writes_nothing_and_says_what_it_would_do(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            before = {p.name for p in repo.rglob("*")}
            code, out, _ = run_scaffold(repo, "service", "--dry-run")
            self.assertEqual(code, 0)
            self.assertIn("would write deploy/deploy.sh", out)
            self.assertEqual({p.name for p in repo.rglob("*")}, before)
            self.assertNotIn("deploy.local.env", (repo / ".gitignore").read_text() if (repo / ".gitignore").exists() else "")

    def test_gitignore_and_readme_are_updated_once(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            run_scaffold(repo, "job")
            run_scaffold(repo, "job", "--force")
            self.assertEqual((repo / ".gitignore").read_text().count("config/deploy.local.env"), 1)
            readme = (repo / "README.md").read_text()
            self.assertEqual(readme.count("## Ways to run it"), 1)
            self.assertLess(readme.index("Ways to run it"), readme.index("## Usage"))

    def test_the_generated_config_is_really_ignored_by_git(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            run_scaffold(repo, "job")
            done = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "config/deploy.local.env"])
            self.assertEqual(done.returncode, 0)

    def test_invalid_inputs_are_refused_with_a_reason(self) -> None:
        cases = {
            "app": (["--kind", "job", "--app", "Bad_Name"], "--app"),
            "prefix": (["--kind", "job", "--app", "ok", "--prefix", "lower"], "--prefix"),
            "job unit must be a timer": (["--kind", "job", "--app", "ok", "--unit", "ok.service"], "timer"),
            "worker unit must be a service": (["--kind", "worker", "--app", "ok", "--unit", "ok.timer"], "service"),
            "service needs an address": (["--kind", "service", "--app", "ok"], "--cluster-ip and --port"),
            "registry address": (["--kind", "service", "--app", "ok", "--cluster-ip", "10.43.0.50", "--port", "5599"], "10.43.0.51-10.43.0.99"),
            "outside the range": (["--kind", "service", "--app", "ok", "--cluster-ip", "10.43.5.5", "--port", "5599"], "10.43.0.51-10.43.0.99"),
            "address already taken": (["--kind", "service", "--app", "ok", "--cluster-ip", "10.43.0.60", "--port", "5599"], "already in docs"),
            "port already taken": (["--kind", "service", "--app", "ok", "--cluster-ip", "10.43.0.93", "--port", "5434"], "already in docs"),
            "reserved port": (["--kind", "service", "--app", "ok", "--cluster-ip", "10.43.0.93", "--port", "9090"], "belongs to"),
            "bad drop-in env": (["--kind", "sandbox", "--app", "ok", "--cluster-ip", "10.43.0.93", "--port", "5599", "--dropin-env", "nokey"], "KEY=VALUE"),
        }
        for name, (argv, reason) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                repo = make_repo(root)
                err = StringIO()
                with redirect_stderr(err), redirect_stdout(StringIO()):
                    code = scaffold.main(["--repo", str(repo), *argv])
                self.assertEqual(code, 2, err.getvalue())
                self.assertIn(reason, err.getvalue())
                self.assertFalse((repo / "deploy").exists(), "a refused scaffold writes nothing")

    def test_the_address_table_is_read_from_the_pattern_doc(self) -> None:
        ips, ports = scaffold.used_addresses()
        self.assertIn("10.43.0.50", ips)
        self.assertIn(5000, ports)

    def test_an_unfilled_placeholder_is_an_error_not_a_file_with_holes_in_it(self) -> None:
        with self.assertRaises(scaffold.ScaffoldError) as caught:
            scaffold.render("hello @@MISSING@@", {"APP": "x"})
        self.assertIn("MISSING", str(caught.exception))

    def test_every_template_placeholder_is_one_the_generator_fills(self) -> None:
        import re

        known = set()
        with tempfile.TemporaryDirectory() as root:
            for kind in KINDS:
                repo = make_repo(root, f"r-{kind}")
                self.assertEqual(run_scaffold(repo, kind)[0], 0)
                for path in repo.rglob("*"):
                    if path.is_file() and ".git/" not in str(path):
                        known |= set(re.findall(r"@@[A-Z_]+@@", path.read_text(errors="ignore")))
        self.assertEqual(known, set(), "no generated file may contain an unfilled placeholder")


class CheckerTests(unittest.TestCase):
    def scaffolded(self, root: str, kind: str) -> Path:
        repo = make_repo(root)
        self.assertEqual(run_scaffold(repo, kind)[0], 0)
        return repo

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def test_missing_set_E_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "worker")
            self.edit(repo / "deploy" / "deploy.sh", "set -eEuo pipefail", "set -euo pipefail")
            self.assertTrue(any("set -eEuo" in f for f in failures(checked(repo))))

    def test_hiding_a_config_read_failure_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            self.edit(repo / "deploy" / "deploy.sh", 'env_value.py" "$CONFIG_FILE"', 'env_value.py" "$CONFIG_FILE" || true #')
            self.assertTrue(any("never hides a failure" in f for f in failures(checked(repo))))

    def test_python_dash_P_in_a_host_script_is_a_failure_but_inside_kubectl_exec_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            verify = repo / "deploy" / "k3s" / "verify.sh"
            verify.write_text(verify.read_text() + '\nkubectl exec pod -- python3 -P -m app status\n')
            self.assertEqual([f for f in failures(checked(repo)) if "-P" in f], [])
            deploy = repo / "deploy" / "deploy.sh"
            deploy.write_text(deploy.read_text() + "\npython3 -P something.py\n")
            self.assertTrue(any("-P" in f for f in failures(checked(repo))))

    def test_a_non_executable_script_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            (repo / "deploy" / "k3s" / "verify.sh").chmod(0o644)
            self.assertTrue(any("executable" in f for f in failures(checked(repo))))

    def test_a_missing_gitignore_rule_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            (repo / ".gitignore").write_text("")
            self.assertTrue(any("gitignored" in f for f in failures(checked(repo))))

    def test_a_build_without_the_dirty_source_guard_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "worker")
            build = repo / "deploy" / "k3s" / "build-image.sh"
            build.write_text(build.read_text().replace("ALLOW_DIRTY", "NOPE"))
            self.assertTrue(any("uncommitted source" in f for f in failures(checked(repo))))

    def test_sudo_is_a_failure_when_called_but_not_when_a_message_mentions_it(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            helper = repo / "deploy" / "k3s" / "apply.sh"
            helper.write_text(helper.read_text() + '\necho "Next, once: sudo deploy/k3s/enable.sh"\n')
            self.assertEqual([f for f in failures(checked(repo)) if "sudo" in f], [])
            helper.write_text(helper.read_text() + "\nsudo systemctl restart k3s\n")
            self.assertTrue(any("sudo" in f for f in failures(checked(repo))))

    def test_job_handing_over_before_verify_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            deploy = repo / "deploy" / "deploy.sh"
            text = deploy.read_text()
            text = text.replace('    "$K3S_DIR/verify.sh"\n', "", 1).replace(
                '    "$K3S_DIR/handover.sh" pod-on\n', '    "$K3S_DIR/handover.sh" pod-on\n    "$K3S_DIR/verify.sh"\n', 1
            )
            deploy.write_text(text)
            self.assertTrue(any("job:" in f for f in failures(checked(repo))))

    def test_worker_without_a_restore_trap_or_that_stops_the_host_late_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "worker")
            self.edit(repo / "deploy" / "deploy.sh", "trap restore ERR", "true")
            self.assertTrue(any("restores" in f for f in failures(checked(repo))))

    def test_sandbox_pointing_the_service_before_verify_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "sandbox")
            deploy = repo / "deploy" / "deploy.sh"
            text = deploy.read_text()
            text = text.replace('    "$K3S_DIR/verify.sh"\n    install_dropin\n', "    install_dropin\n", 1)
            self.assertNotEqual(text, deploy.read_text())
            deploy.write_text(text + "\n")
            self.assertTrue(any("sandbox: the bridge is up and verified" in f for f in failures(checked(repo))))

    def test_service_bridge_before_verify_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "service")
            deploy = repo / "deploy" / "deploy.sh"
            text = deploy.read_text()
            text = text.replace('    "$K3S_DIR/verify.sh"\n', "", 1).replace(
                '    "$K3S_DIR/bridge.sh" on\n', '    "$K3S_DIR/bridge.sh" on\n    "$K3S_DIR/verify.sh"\n', 1
            )
            deploy.write_text(text)
            self.assertTrue(any("service: the bridge comes up only after verify" in f for f in failures(checked(repo))))

    def manifest(self, repo: Path, text: str) -> None:
        folder = repo / "deploy" / "k3s" / "manifests"
        folder.mkdir(exist_ok=True)
        (folder / "app.yaml").write_text(text)

    GOOD = (
        "kind: Namespace\nmetadata:\n  labels:\n    pod-security.kubernetes.io/enforce: restricted\n---\n"
        "kind: CiliumNetworkPolicy\n"
    )

    def test_manifest_rules(self) -> None:
        cases = {
            "fsGroup beside a host volume": (self.GOOD + "kind: PersistentVolume\nspec:\n  local: {path: /x}\nfsGroup: 1\n", "fsGroup"),
            "a hostPath volume": (self.GOOD + "volumes:\n  - hostPath: {path: /x}\n", "hostPath"),
            "a CronJob that is not suspended": (self.GOOD + "kind: CronJob\nspec:\n  suspend: false\n", "suspended"),
            "no restricted namespace": ("kind: CiliumNetworkPolicy\n", "restricted"),
            "no network policy": ("kind: Namespace\nmetadata:\n  labels:\n    pod-security.kubernetes.io/enforce: restricted\n", "network policy"),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                repo = self.scaffolded(root, "job")
                self.manifest(repo, text)
                self.assertTrue(any(needle in f for f in failures(checked(repo))), name)

    def test_fsGroup_is_acceptable_when_no_host_files_are_mounted(self) -> None:
        # A pod that mounts only Secrets needs fsGroup to read them; the rule is about host data.
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            self.manifest(repo, self.GOOD + "spec:\n  securityContext:\n    fsGroup: 10001\n")
            self.assertEqual([f for f in failures(checked(repo)) if "fsGroup" in f], [])

    def test_a_good_manifest_set_passes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            self.manifest(repo, self.GOOD + "kind: CronJob\nspec:\n  suspend: true\n")
            self.assertEqual(failures(checked(repo)), [])

    def test_the_cli_exit_codes_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.scaffolded(root, "job")
            done = subprocess.run([sys.executable, str(SCRIPTS / "check_deployment_modes.py"), "--repo", str(repo), "--json"], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertIn('"status": "PASS"', done.stdout)
            (repo / "deploy" / "deploy.sh").chmod(0o644)
            bad = subprocess.run([sys.executable, str(SCRIPTS / "check_deployment_modes.py"), "--repo", str(repo)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("FAIL", bad.stdout)
            missing = subprocess.run([sys.executable, str(SCRIPTS / "check_deployment_modes.py"), "--repo", str(repo / "nope")], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)

    def test_a_repository_with_no_deploy_script_fails_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            report = checked(repo)
            self.assertTrue(report.failed)
            self.assertEqual(len(report.results), 1)


if __name__ == "__main__":
    unittest.main()


class TriggeredKindTests(unittest.TestCase):
    def test_the_timer_is_never_disabled_and_the_dropin_replaces_only_execstart(self):
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            self.assertEqual(run_scaffold(repo, "triggered")[0], 0)
            deploy = (repo / "deploy" / "deploy.sh").read_text()
            up_k3s = deploy[deploy.index("  up:k3s)") : deploy.index("  down:k3s)")]
            self.assertNotIn("disable --now", up_k3s)
            self.assertTrue((repo / "deploy" / "k3s" / "run-job.sh").exists())
            dropin = (repo / "deploy" / "k3s" / "systemd" / "zz-k3s-run.conf").read_text()
            self.assertIn("ExecStart=\n", dropin)
            self.assertEqual(failures(checked(repo)), [])

    def test_the_checker_flags_a_triggered_deployment_that_disables_its_timer(self):
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            run_scaffold(repo, "triggered")
            path = repo / "deploy" / "deploy.sh"
            path.write_text(path.read_text().replace('    "$K3S_DIR/handover.sh" pod-on\n', '    systemctl --user disable --now "$TIMER"\n    "$K3S_DIR/handover.sh" pod-on\n', 1))
            self.assertTrue(any("never disabled" in f for f in failures(checked(repo))))

    def test_the_checker_flags_a_dropin_that_changes_more_than_execstart(self):
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            run_scaffold(repo, "triggered")
            conf = repo / "deploy" / "k3s" / "systemd" / "zz-k3s-run.conf"
            conf.write_text(conf.read_text() + "ExecCondition=/bin/true\n")
            self.assertTrue(any("only ExecStart" in f for f in failures(checked(repo))))

    def test_a_triggered_unit_must_be_a_timer(self):
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            code, _, err = run_scaffold(repo, "triggered", "--unit", "x.service")
            self.assertEqual(code, 2)
            self.assertIn("timer", err)

    def test_the_build_script_finds_crew_chief_outside_util_repos(self):
        with tempfile.TemporaryDirectory() as root:
            repo = make_repo(root)
            run_scaffold(repo, "job")
            text = (repo / "deploy" / "k3s" / "build-image.sh").read_text()
            self.assertIn("../../util-repos/crew-chief", text)


class AddressRegistryTests(unittest.TestCase):
    def test_only_the_address_table_claims_addresses_not_examples_or_notes(self):
        ips, ports = scaffold.used_addresses()
        # The doc's example command and its "next free" note mention addresses; only table rows are claims.
        text = scaffold.DOCS.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line.lstrip().startswith("|"):
                for ip in __import__("re").findall(r"10\.43\.0\.\d+", line):
                    if not any(ip in row for row in text.splitlines() if row.lstrip().startswith("|")):
                        self.assertNotIn(ip, ips)
        self.assertIn("10.43.0.62", ips)  # the torrent web UI is in the table
        self.assertIn(5400, ports)
