"""Tests for job_guard: when a job may start, and what the installer writes."""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import job_guard as guard  # noqa: E402

COOL = guard.Readings(load_per_core=0.10, cpu_temp_c=45.0, gpu_temp_c=40.0)
LIMITS = guard.Limits()
HEAVY = guard.UnitPolicy("heavy", "3h")
LIGHT = guard.UnitPolicy("light", "15min")
EXEMPT = guard.UnitPolicy("exempt", None)


class DecideTests(unittest.TestCase):
    def test_heavy_job_runs_on_a_cool_idle_machine(self) -> None:
        self.assertEqual(guard.decide(HEAVY, COOL, LIMITS)[0], True)

    def test_heavy_job_waits_for_each_kind_of_pressure(self) -> None:
        cases = {
            "hot cpu": guard.Readings(0.1, 82.0, 40.0),
            "hot gpu": guard.Readings(0.1, 45.0, 85.0),
            "busy": guard.Readings(0.95, 45.0, 40.0),
            "critical cpu": guard.Readings(0.1, 98.0, 40.0),
        }
        for label, readings in cases.items():
            with self.subTest(label):
                run, reason = guard.decide(HEAVY, readings, LIMITS)
                self.assertFalse(run, reason)

    def test_heavy_job_waits_for_an_earlier_heavy_peer(self) -> None:
        run, reason = guard.decide(HEAVY, COOL, LIMITS, earlier_heavy_peers=["render.service"])
        self.assertFalse(run)
        self.assertIn("render.service", reason)

    def test_light_job_ignores_everything_but_the_critical_temperature(self) -> None:
        busy_and_hot = guard.Readings(2.0, 85.0, 90.0)
        self.assertTrue(guard.decide(LIGHT, busy_and_hot, LIMITS, earlier_heavy_peers=["x"])[0])
        self.assertFalse(guard.decide(LIGHT, guard.Readings(0.1, 91.0, None), LIMITS)[0])

    def test_exempt_job_is_never_held(self) -> None:
        self.assertTrue(guard.decide(EXEMPT, guard.Readings(9.0, 99.0, 99.0), LIMITS)[0])

    def test_unreadable_sensors_never_block_a_job(self) -> None:
        self.assertTrue(guard.decide(HEAVY, guard.Readings(None, None, None), LIMITS)[0])


class PeerOrderTests(unittest.TestCase):
    def test_exactly_one_of_two_simultaneous_jobs_proceeds(self) -> None:
        peers = [guard.Peer("a.service", 100), guard.Peer("b.service", 100)]
        self.assertEqual(guard.peers_to_wait_for("a.service", 100, peers), [])
        self.assertEqual(guard.peers_to_wait_for("b.service", 100, peers), ["a.service"])

    def test_later_job_yields_to_the_one_already_running(self) -> None:
        peers = [guard.Peer("old.service", 50), guard.Peer("new.service", 900)]
        self.assertEqual(guard.peers_to_wait_for("new.service", 900, peers), ["old.service"])
        self.assertEqual(guard.peers_to_wait_for("old.service", 50, peers), [])


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"
        self.addCleanup(self._tmp.cleanup)


class CheckTests(TempDirCase):
    def run_check(self, readings, *, active=None, config=None, unit="job.service"):
        retries: list[tuple[str, int]] = []

        def retry(name: str, minutes: int) -> bool:
            retries.append((name, minutes))
            return True

        code, message = guard.check(
            unit,
            config or guard.Config(),
            self.state,
            readings=readings,
            active=active if active is not None else {unit: 10},
            retry=retry,
            now=1000.0,
            rng=random.Random(0),
        )
        return code, message, retries

    def test_deferral_schedules_a_retry_and_counts(self) -> None:
        hot = guard.Readings(0.1, 85.0, None)
        code, message, retries = self.run_check(hot)
        self.assertEqual(code, guard.DEFER)
        self.assertEqual(retries[0][0], "job.service")
        self.assertGreaterEqual(retries[0][1], LIMITS.retry_minutes)
        self.assertLessEqual(retries[0][1], LIMITS.retry_minutes + LIMITS.retry_jitter_minutes)
        self.assertEqual(guard.read_state(self.state, "job.service")["deferrals"], 1)
        self.assertIn("deferred 1/6", message)

        self.run_check(hot)
        state = guard.read_state(self.state, "job.service")
        self.assertEqual(state["deferrals"], 2)
        self.assertEqual(state["first_deferred_at"], 1000.0)

    def test_running_clears_the_deferral_count(self) -> None:
        self.run_check(guard.Readings(0.1, 85.0, None))
        code, _, retries = self.run_check(COOL)
        self.assertEqual(code, guard.RUN)
        self.assertEqual(retries, [])
        self.assertEqual(guard.read_state(self.state, "job.service"), {})

    def test_gives_up_after_the_deferral_cap_without_another_retry(self) -> None:
        hot = guard.Readings(0.1, 85.0, None)
        for _ in range(LIMITS.max_deferrals):
            self.run_check(hot)
        code, message, retries = self.run_check(hot)
        self.assertEqual(code, guard.DEFER)
        self.assertEqual(retries, [])
        self.assertIn("next scheduled run", message)
        self.assertEqual(guard.read_state(self.state, "job.service"), {})

    def test_heavy_job_defers_to_a_recorded_heavy_peer_but_not_a_light_one(self) -> None:
        self.state.mkdir()
        (self.state / "guarded-units.json").write_text(
            '{"job.service": "heavy", "render.service": "heavy", "poll.service": "light"}'
        )
        active = {"job.service": 500, "render.service": 100, "poll.service": 50}
        code, message, _ = self.run_check(COOL, active=active)
        self.assertEqual(code, guard.DEFER)
        self.assertIn("render.service", message)
        self.assertNotIn("poll.service", message)

        code, _, _ = self.run_check(COOL, active={"job.service": 500, "poll.service": 50})
        self.assertEqual(code, guard.RUN)


class ConfigTests(TempDirCase):
    def write(self, body: str) -> Path:
        path = self.root / "config.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_missing_file_means_defaults(self) -> None:
        config = guard.load_config(self.root / "absent.toml")
        self.assertEqual(config.policy("anything.service"), guard.UnitPolicy("heavy", "3h"))

    def test_example_config_loads_and_overrides_per_unit(self) -> None:
        example = Path(__file__).resolve().parents[1] / "config/job-guard/config.example.toml"
        config = guard.load_config(example)
        self.assertEqual(config.limits, guard.Limits())
        self.assertEqual(config.policy("example-mail-poller.service"), LIGHT)
        self.assertEqual(config.policy("unlisted.service").job_class, "heavy")

    def test_bad_configs_are_rejected(self) -> None:
        bodies = (
            '[defaults]\nclass = "urgent"\n',
            '[defaults]\ntimeout = "soon"\n',
            "[limits]\ncpu_temp_hot_c = -5\n",
            "[limits]\nmade_up = 1\n",
            "[limits]\ncpu_temp_hot_c = 95\ncpu_temp_critical_c = 90\n",
            "not toml = = =\n",
        )
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(guard.GuardError):
                guard.load_config(self.write(body))

    def test_check_fails_open_on_a_broken_config(self) -> None:
        code = guard.main(
            ["--config", str(self.write("[limits]\nmade_up = 1\n")), "--state-dir", str(self.state),
             "check", "--unit", "job.service"]
        )
        self.assertEqual(code, guard.RUN)


class InstallTests(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.units = self.root / "units"
        self.units.mkdir()
        self.script = Path("/opt/portfolio/scripts/job_guard.py")

    def unit(self, name: str, body: str = "[Service]\nType=oneshot\nExecStart=/bin/true\n") -> None:
        (self.units / name).write_text(body, encoding="utf-8")

    def install(self, config=None, **kwargs):
        options = {"remove": False, "dry_run": False} | kwargs
        return guard.install(self.units, config or guard.Config(), self.state, self.script, **options)

    def test_only_timer_driven_services_get_a_dropin(self) -> None:
        self.unit("nightly.service")
        self.unit("nightly.timer", "[Timer]\nOnCalendar=daily\n")
        self.unit("daemon.service")
        self.unit("renamed.timer", "[Timer]\nUnit=other.service\n")
        self.unit("other.service")
        self.unit("orphan.timer", "[Timer]\nOnCalendar=daily\n")

        self.install()

        dropins = sorted(p.parent.name for p in self.units.glob(f"*.d/{guard.DROPIN_NAME}"))
        self.assertEqual(dropins, ["nightly.service.d", "other.service.d"])
        body = (self.units / "nightly.service.d" / guard.DROPIN_NAME).read_text()
        self.assertIn(f"ExecCondition=/usr/bin/python3 {self.script} check --unit %n", body)
        self.assertIn("TimeoutStartSec=3h", body)
        self.assertNotIn("RuntimeMaxSec", body)
        recorded = guard.guarded_units(self.state)
        self.assertEqual(recorded, {"nightly.service": "heavy", "other.service": "heavy"})

    def test_the_time_limit_key_matches_the_service_type(self) -> None:
        # systemd ignores RuntimeMaxSec on a oneshot unit and logs a warning on
        # every reload; a long-running type ignores TimeoutStartSec once started.
        self.unit("batch.service")  # Type=oneshot
        self.unit("batch.timer", "[Timer]\nOnCalendar=daily\n")
        self.unit("worker.service", "[Service]\nExecStart=/bin/true\n")  # default type
        self.unit("worker.timer", "[Timer]\nOnCalendar=daily\n")
        self.unit("inst@.service", "[Service]\nType=oneshot\nExecStart=/bin/true\n")
        self.unit("inst@a.timer", "[Timer]\nOnCalendar=daily\n")

        self.install()

        def body(name: str) -> str:
            return (self.units / f"{name}.d" / guard.DROPIN_NAME).read_text()

        self.assertIn("TimeoutStartSec=3h", body("batch.service"))
        self.assertNotIn("RuntimeMaxSec", body("batch.service"))
        self.assertIn("RuntimeMaxSec=3h", body("worker.service"))
        self.assertNotIn("TimeoutStartSec", body("worker.service"))
        self.assertIn("TimeoutStartSec=3h", body("inst@a.service"))
        self.assertNotIn("RuntimeMaxSec", body("inst@a.service"))

    def test_a_unit_that_sets_its_own_limit_keeps_it(self) -> None:
        self.unit("slow.service", "[Service]\nType=oneshot\nTimeoutStartSec=7200\n")
        self.unit("slow.timer", "[Timer]\nOnCalendar=weekly\n")

        report = self.install()

        body = (self.units / "slow.service.d" / guard.DROPIN_NAME).read_text()
        self.assertIn("ExecCondition=", body)
        self.assertNotIn("TimeoutStartSec", body)
        self.assertIn("own limit kept", report[0])

    def test_a_unit_that_already_carries_the_check_is_not_checked_twice(self) -> None:
        # clockwork renders `start_condition` into the unit itself.
        self.unit(
            "native.service",
            "[Service]\nType=oneshot\n"
            f"ExecCondition=/usr/bin/python3 {self.script} check --unit %n\n"
            "ExecStart=/bin/true\n",
        )
        self.unit("native.timer", "[Timer]\nOnCalendar=daily\n")
        self.unit(
            "other.service",
            "[Service]\nType=oneshot\nExecCondition=/usr/bin/test -f /ready\nExecStart=/bin/true\n",
        )
        self.unit("other.timer", "[Timer]\nOnCalendar=daily\n")

        report = self.install()

        native = (self.units / "native.service.d" / guard.DROPIN_NAME).read_text()
        self.assertNotIn("ExecCondition", native)
        self.assertIn("TimeoutStartSec=3h", native)
        self.assertTrue(any("native.service" in r and "own check kept" in r for r in report))
        # An unrelated condition is not this guard, so the guard is still added.
        other = (self.units / "other.service.d" / guard.DROPIN_NAME).read_text()
        self.assertIn(f"ExecCondition=/usr/bin/python3 {self.script} check --unit %n", other)

    def test_template_instances_and_per_unit_policy(self) -> None:
        self.unit("snap@.service")
        self.unit("snap@noon.timer", "[Timer]\nOnCalendar=12:00\n")
        self.unit("mail.service")
        self.unit("mail.timer", "[Timer]\nOnBootSec=5m\n")
        config = guard.Config(units={"mail": EXEMPT, "snap@noon": LIGHT})

        self.install(config)

        snap = (self.units / "snap@noon.service.d" / guard.DROPIN_NAME).read_text()
        self.assertIn("TimeoutStartSec=15min", snap)
        mail = (self.units / "mail.service.d" / guard.DROPIN_NAME).read_text()
        self.assertNotIn("ExecCondition", mail)
        self.assertNotIn("TimeoutStartSec", mail)

    def test_dry_run_writes_nothing_and_remove_undoes_install(self) -> None:
        self.unit("nightly.service")
        self.unit("nightly.timer", "[Timer]\nOnCalendar=daily\n")

        self.assertTrue(self.install(dry_run=True))
        self.assertEqual(list(self.units.glob("*.d")), [])

        self.install()
        foreign = self.units / "nightly.service.d" / "10-mine.conf"
        foreign.write_text("[Service]\nNice=5\n")
        self.install(remove=True)
        self.assertFalse((self.units / "nightly.service.d" / guard.DROPIN_NAME).exists())
        self.assertTrue(foreign.exists(), "a drop-in this tool did not write must survive")
        self.assertEqual(guard.guarded_units(self.state), {})


class SensorTests(TempDirCase):
    def test_cpu_temperature_ignores_the_board_zone(self) -> None:
        for index, (zone_type, value) in enumerate([("acpitz", 27800), ("x86_pkg_temp", 91000)]):
            zone = self.root / "thermal" / f"thermal_zone{index}"
            zone.mkdir(parents=True)
            (zone / "type").write_text(zone_type + "\n")
            (zone / "temp").write_text(f"{value}\n")
        self.assertEqual(guard.read_cpu_temp(self.root), 91.0)
        self.assertIsNone(guard.read_cpu_temp(self.root / "absent"))


if __name__ == "__main__":
    unittest.main()
