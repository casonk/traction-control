"""Tests for resource_limits: deriving limits from history, and refusing unrecorded raises."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import resource_limits as rl  # noqa: E402

JOURNAL = [
    "2026-11-01T10:00:00-04:00 desk systemd[1]: Starting job-a.service - A...",
    "2026-11-01T10:10:00-04:00 desk systemd[1]: job-a.service: Consumed 5min 0s CPU time, 1.2G memory peak.",
    "2026-11-02T10:00:00-04:00 desk systemd[1]: Starting job-a.service - A...",
    "2026-11-02T10:01:00-04:00 desk systemd[1]: job-a.service: Consumed 30s CPU time, 2.0G memory peak.",
    "2026-11-01T11:00:00-04:00 desk systemd[1]: Starting job-b.service - B...",
    "2026-11-01T11:00:09-04:00 desk systemd[1]: job-b.service: Consumed 812ms CPU time, 41.4M memory peak.",
    "2026-11-01T12:00:00-04:00 desk systemd[1]: run-p1-i2.scope: Consumed 9s CPU time, 5M memory peak.",
]


class ParseTests(unittest.TestCase):
    def test_cpu_time_and_memory_units(self) -> None:
        self.assertAlmostEqual(rl.parse_cpu_seconds("1h 2min 3.5s"), 3723.5)
        self.assertAlmostEqual(rl.parse_cpu_seconds("812ms"), 0.812)
        self.assertAlmostEqual(rl.parse_mib("2.0", "G"), 2048.0)
        self.assertAlmostEqual(rl.parse_mib("512", "K"), 0.5)

    def test_peaks_and_the_busiest_run_per_unit(self) -> None:
        seen = rl.observe_lines(JOURNAL)
        a = seen["job-a.service"]
        self.assertEqual(a["runs"], 2)
        self.assertAlmostEqual(a["peak_mib"], 2048.0)
        self.assertAlmostEqual(
            a["mean_cores"], 0.5
        )  # 30 s CPU in 60 s beats 300 s in 600 s
        self.assertNotIn("run-p1-i2.scope", seen)

    def test_a_filter_keeps_only_the_named_units(self) -> None:
        self.assertEqual(
            set(rl.observe_lines(JOURNAL, {"job-b.service"})), {"job-b.service"}
        )


class DeriveTests(unittest.TestCase):
    def test_memory_gets_headroom_in_steps_and_cpu_a_floor(self) -> None:
        seen = {"runs": 20, "peak_mib": 2048.0, "max_cpu_s": 300.0, "mean_cores": 0.5}
        limits = rl.derive(seen, "heavy")
        self.assertEqual(limits["memory_max"], 3072)  # 2048 * 1.5
        self.assertEqual(
            limits["cpu_quota"], 200
        )  # 0.5 cores * 3 = 150: the heavy floor

    def test_thin_evidence_never_goes_below_the_class_default(self) -> None:
        few = {"runs": 3, "peak_mib": 31.0, "max_cpu_s": 1.0, "mean_cores": 0.1}
        self.assertEqual(rl.derive(few, "heavy")["memory_max"], 4096)
        self.assertIn("under 5 runs", rl.derive(few, "heavy")["note"])
        self.assertEqual(rl.derive({**few, "runs": 40}, "heavy")["memory_max"], 512)

    def test_a_cpu_hungry_job_gets_a_larger_quota_up_to_the_ceiling(self) -> None:
        busy = {"runs": 3, "peak_mib": 100.0, "max_cpu_s": 900.0, "mean_cores": 3.0}
        self.assertEqual(rl.derive(busy, "heavy")["cpu_quota"], 800)  # 900 -> ceiling
        busy["mean_cores"] = 1.2
        self.assertEqual(rl.derive(busy, "heavy")["cpu_quota"], 400)  # 360 -> 400

    def test_a_small_job_still_gets_the_memory_floor(self) -> None:
        tiny = rl.observe_lines(JOURNAL)["job-b.service"]
        self.assertEqual(rl.derive({**tiny, "runs": 30}, "light")["memory_max"], 512)

    def test_exempt_jobs_get_a_memory_ceiling_but_no_cpu_quota(self) -> None:
        self.assertNotIn("cpu_quota", rl.derive(None, "exempt"))

    def test_no_history_means_class_defaults_and_says_so(self) -> None:
        limits = rl.derive(None, "heavy")
        self.assertEqual((limits["memory_max"], limits["cpu_quota"]), (4096, 200))
        self.assertIn("no history", limits["note"])


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "limits"
        self.units_file = Path(self._tmp.name) / "units.json"
        self.units_file.write_text(
            json.dumps({"job-a.service": "heavy", "job-b.service": "light"})
        )

    def baseline(self, write: bool = True) -> int:
        args = argparse.Namespace(
            dir=self.dir, units_file=self.units_file, days=30, write=write
        )
        return rl.cmd_baseline(args, JOURNAL)

    def change(self, command: str, unit: str = "job-a", **kw) -> int:
        values = {
            "cpu_quota": None,
            "memory_max": None,
            "companions": None,
            "nice": None,
            "reason": "",
            "evidence": "",
            "approved_by": "tester",
        }
        values.update(kw)
        return rl.cmd_change(
            argparse.Namespace(dir=self.dir, command=command, unit=unit, **values)
        )


class BaselineTests(WorkspaceCase):
    def test_dry_run_writes_nothing(self) -> None:
        self.assertEqual(self.baseline(write=False), 0)
        self.assertFalse(rl.registry_path(self.dir).exists())

    def test_write_records_registry_and_a_chained_audit_log(self) -> None:
        self.baseline()
        registry = rl.read_registry(self.dir)
        self.assertEqual(registry["job-a"]["memory_max"], 4096)
        entries = rl.read_audit(self.dir)
        self.assertEqual({e["unit"] for e in entries}, {"job-a", "job-b"})
        self.assertIsNone(rl.verify_chain(entries))
        self.assertEqual(rl.check(registry, entries)[0], [])

    def test_a_second_baseline_leaves_existing_limits_alone(self) -> None:
        self.baseline()
        before = rl.audit_path(self.dir).read_text()
        self.baseline()
        self.assertEqual(rl.audit_path(self.dir).read_text(), before)


class RegulationTests(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.baseline()

    def test_a_hand_edited_raise_fails_the_check(self) -> None:
        registry = rl.read_registry(self.dir)
        registry["job-a"]["memory_max"] = 9999
        rl.write_registry(self.dir, registry)
        failures, _ = rl.check(rl.read_registry(self.dir), rl.read_audit(self.dir))
        self.assertEqual(len(failures), 1)
        self.assertIn("memory_max is 9999, approved 4096", failures[0])

    def test_raise_needs_a_reason_and_evidence(self) -> None:
        with self.assertRaises(rl.LimitsError):
            self.change("raise", memory_max=6144)
        with self.assertRaises(rl.LimitsError):
            self.change("raise", memory_max=6144, reason="grew")
        self.assertEqual(rl.read_registry(self.dir)["job-a"]["memory_max"], 4096)

    def test_a_recorded_raise_passes_and_shows_in_the_history(self) -> None:
        self.change(
            "raise", memory_max=6144, reason="input doubled", evidence="peak 4000 MiB"
        )
        registry, entries = rl.read_registry(self.dir), rl.read_audit(self.dir)
        self.assertEqual(registry["job-a"]["memory_max"], 6144)
        self.assertEqual(rl.check(registry, entries)[0], [])
        self.assertEqual(entries[-1]["old"], {"memory_max": 4096})

    def test_lowering_is_free_but_cannot_be_used_to_raise(self) -> None:
        self.change("lower", memory_max=2048)
        self.assertEqual(rl.read_registry(self.dir)["job-a"]["memory_max"], 2048)
        with self.assertRaises(rl.LimitsError):
            self.change("lower", memory_max=8192)
        with self.assertRaises(rl.LimitsError):
            self.change("raise", memory_max=1024, reason="x", evidence="y")

    def test_raising_back_up_after_a_lowering_still_needs_evidence(self) -> None:
        self.change("lower", memory_max=2048)
        with self.assertRaises(rl.LimitsError):
            self.change("raise", memory_max=4096)
        self.change("raise", memory_max=4096, reason="r", evidence="e")

    def test_dropping_a_ceiling_counts_as_raising_it(self) -> None:
        registry = rl.read_registry(self.dir)
        del registry["job-a"]["cpu_quota"]
        failures, _ = rl.check(registry, rl.read_audit(self.dir))
        self.assertTrue(any("cpu_quota" in f for f in failures))

    def test_editing_the_audit_log_is_detected(self) -> None:
        path = rl.audit_path(self.dir)
        lines = path.read_text().splitlines()
        first = json.loads(lines[0])
        first["new"]["memory_max"] = 99999
        lines[0] = json.dumps(first, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
        failures, _ = rl.check(rl.read_registry(self.dir), rl.read_audit(self.dir))
        self.assertIn("audit log", failures[0])
        with self.assertRaises(rl.LimitsError):
            self.change(
                "lower", memory_max=100
            )  # nothing may be appended to a broken log

    def test_a_job_without_an_approved_baseline_fails(self) -> None:
        registry = rl.read_registry(self.dir)
        registry["stranger"] = {"memory_max": 100}
        failures, _ = rl.check(registry, rl.read_audit(self.dir))
        self.assertIn("no approved baseline", failures[0])

    def test_peaks_near_or_over_the_ceiling_warn(self) -> None:
        registry, entries = rl.read_registry(self.dir), rl.read_audit(self.dir)
        near = {"job-a": {"runs": 1, "peak_mib": 3700.0}}
        over = {"job-a": {"runs": 1, "peak_mib": 4200.0}}
        self.assertIn("within 15%", rl.check(registry, entries, near)[1][0])
        self.assertIn("reached its", rl.check(registry, entries, over)[1][0])
        self.assertEqual(
            rl.check(registry, entries, {"job-a": {"runs": 1, "peak_mib": 100.0}})[1],
            [],
        )


class CliTests(WorkspaceCase):
    def test_check_exit_codes(self) -> None:
        self.baseline()
        args = argparse.Namespace(dir=self.dir, days=30, no_journal=True)
        self.assertEqual(rl.cmd_check(args), 0)
        registry = rl.read_registry(self.dir)
        registry["job-a"]["memory_max"] = 10**6
        rl.write_registry(self.dir, registry)
        self.assertEqual(rl.cmd_check(args), 1)

    def test_main_reports_errors_without_a_traceback(self) -> None:
        self.assertEqual(
            rl.main(["--dir", str(self.dir), "raise", "job-a", "--memory-max", "1"]), 2
        )

    def test_the_example_registry_is_valid_toml_in_the_registry_shape(self) -> None:
        import tomllib

        example = (
            Path(__file__).resolve().parents[1]
            / "config/resource-limits/limits.example.toml"
        )
        units = tomllib.loads(example.read_text())["units"]
        for limits in units.values():
            self.assertLessEqual(set(limits), set(rl.FIELDS) | {"note"})


if __name__ == "__main__":
    unittest.main()
