"""Unit coverage for the stateful Traction Control launchd adapter."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO_ROOT / "scripts" / "run_traction_control_job.py"
SPEC = importlib.util.spec_from_file_location("traction_launchd_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _args(state_dir: Path, **overrides):
    values = {
        "job": "test-job",
        "state_dir": state_dir,
        "interval_seconds": 100,
        "startup_delay_seconds": 0,
        "retry_seconds": 300,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _close(fd):
    if fd is not None:
        os.close(fd)


class LaunchdRunnerTests(unittest.TestCase):
    def test_late_load_is_due_immediately_from_boot_relative_delay(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            due, lock_fd, state_path = runner._prepare_due_interval(
                _args(state_dir, startup_delay_seconds=100),
                now_epoch=1000,
                current_boot_id="boot-a",
                boot_epoch=100,
            )
            try:
                self.assertTrue(due)
                self.assertIsNotNone(state_path)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["startup_not_before"], 1000)
                self.assertEqual(state["next_due_epoch"], 1000)
            finally:
                _close(lock_fd)

    def test_pre_delay_load_waits_only_for_remaining_boot_delay(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            due, lock_fd, state_path = runner._prepare_due_interval(
                _args(state_dir, startup_delay_seconds=100),
                now_epoch=1050,
                current_boot_id="boot-a",
                boot_epoch=1000,
            )
            self.assertFalse(due)
            self.assertIsNone(lock_fd)
            self.assertIsNone(state_path)
            state = json.loads(
                (state_dir / "test-job" / "schedule.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["startup_not_before"], 1100)
            self.assertEqual(state["next_due_epoch"], 1100)

    def test_failure_stays_due_but_retries_only_after_poll_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            args = _args(state_dir)
            due, lock_fd, _ = runner._prepare_due_interval(
                args, now_epoch=1000, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertTrue(due)
            _close(lock_fd)  # A failed workload records no success.

            due, lock_fd, _ = runner._prepare_due_interval(
                args, now_epoch=1299, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertFalse(due)
            self.assertIsNone(lock_fd)

            due, lock_fd, state_path = runner._prepare_due_interval(
                args, now_epoch=1300, current_boot_id="boot-a", boot_epoch=900
            )
            try:
                self.assertTrue(due)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["next_due_epoch"], 1000)
            finally:
                _close(lock_fd)

    def test_success_starts_interval_from_success_and_sleep_catches_up_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            args = _args(state_dir)
            due, lock_fd, state_path = runner._prepare_due_interval(
                args, now_epoch=1000, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertTrue(due)
            runner._record_success(
                state_path, job=args.job, success_epoch=1005, interval_seconds=100
            )
            _close(lock_fd)

            due, lock_fd, state_path = runner._prepare_due_interval(
                args, now_epoch=5000, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertTrue(due)
            runner._record_success(
                state_path, job=args.job, success_epoch=5002, interval_seconds=100
            )
            _close(lock_fd)

            due, lock_fd, _ = runner._prepare_due_interval(
                args, now_epoch=5002, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertFalse(due)
            self.assertIsNone(lock_fd)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["next_due_epoch"], 5102)

    def test_lock_serializes_due_checks_and_state_is_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            args = _args(state_dir)
            due, first_lock, state_path = runner._prepare_due_interval(
                args, now_epoch=1000, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertTrue(due)
            due, second_lock, _ = runner._prepare_due_interval(
                args, now_epoch=1000, current_boot_id="boot-a", boot_epoch=900
            )
            self.assertFalse(due)
            self.assertIsNone(second_lock)
            self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((state_dir / "test-job").stat().st_mode), 0o700
            )
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((state_dir / "test-job" / "schedule.lock").stat().st_mode),
                0o600,
            )
            _close(first_lock)

    def test_unsafe_state_file_and_directory_modes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state_dir.mkdir(mode=0o755)
            state_dir.chmod(0o755)
            with self.assertRaisesRegex(runner.RunnerError, "owner-only"):
                runner._prepare_due_interval(
                    _args(state_dir),
                    now_epoch=1000,
                    current_boot_id="boot-a",
                    boot_epoch=900,
                )

        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            job_dir = state_dir / "test-job"
            job_dir.mkdir(parents=True, mode=0o700)
            state_dir.chmod(0o700)
            source = job_dir / "source.json"
            source.write_text("{}\n", encoding="utf-8")
            source.chmod(0o600)
            (job_dir / "schedule.json").symlink_to(source)
            with self.assertRaisesRegex(runner.RunnerError, "refusing scheduler state"):
                runner._prepare_due_interval(
                    _args(state_dir),
                    now_epoch=1000,
                    current_boot_id="boot-a",
                    boot_epoch=900,
                )

    def test_removed_shell_environment_and_delay_flags_fail_closed(self):
        for obsolete in ("--env-file", "--delay-seconds"):
            with (
                self.subTest(obsolete=obsolete),
                self.assertRaisesRegex(runner.RunnerError, "removed"),
            ):
                runner.parse_args(
                    [
                        "--job",
                        "test-job",
                        "--schedule-kind",
                        "none",
                        obsolete,
                        "value",
                        "--",
                        "/usr/bin/true",
                    ]
                )

    def test_non_due_poll_skips_jitter_network_and_workload(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            argv = [
                "--job",
                "test-job",
                "--schedule-kind",
                "interval",
                "--interval-seconds",
                "100",
                "--startup-delay-seconds",
                "100",
                "--jitter-seconds",
                "30",
                "--state-dir",
                str(state_dir),
                "--network-host",
                "example.test",
                "--",
                "/usr/bin/true",
            ]
            with (
                mock.patch.object(
                    runner, "boot_metadata", return_value=("boot-a", 1000)
                ),
                mock.patch.object(runner.time, "time", return_value=1050),
                mock.patch.object(runner.time, "sleep") as sleep,
                mock.patch.object(runner.subprocess, "run") as run_command,
            ):
                self.assertEqual(runner.run(argv), 0)
            sleep.assert_not_called()
            run_command.assert_not_called()

    def test_calendar_jitter_runs_only_for_a_calendar_firing(self):
        argv = [
            "--job",
            "test-job",
            "--schedule-kind",
            "calendar",
            "--jitter-seconds",
            "30",
            "--",
            "/usr/bin/true",
        ]
        random_source = mock.Mock()
        random_source.randrange.return_value = 7
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(
                runner.random, "SystemRandom", return_value=random_source
            ),
            mock.patch.object(runner.time, "sleep") as sleep,
            mock.patch.object(
                runner.subprocess, "run", return_value=completed
            ) as run_command,
        ):
            self.assertEqual(runner.run(argv), 0)
        sleep.assert_called_once_with(7)
        run_command.assert_called_once_with(["/usr/bin/true"], check=False)


if __name__ == "__main__":
    unittest.main()
