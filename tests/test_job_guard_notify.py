"""Tests for job_guard_notify: what gets reported, once, and where it goes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import job_guard as guard  # noqa: E402
import job_guard_notify as notify  # noqa: E402

NOW = 100_000.0
ENV = {
    "JOB_GUARD_NOTIFY_SIGNAL_TARGET": "+10000000000",
    "JOB_GUARD_NOTIFY_GMAIL_TARGET": "owner@example.com",
}


class NotifyCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state"
        self.state.mkdir()
        self.sent: list[tuple[str, str, str]] = []

    def sender(self, root, route, subject, body):
        self.sent.append((route.service, subject, body))
        return None

    def drop(self, unit: str, t: float) -> None:
        guard.record_event(
            self.state, {"t": t, "kind": "dropped", "unit": unit, "deferrals": 6, "reason": "hot"}
        )

    def defer(self, unit: str, since: float, count: int = 3) -> None:
        (self.state / "guarded-units.json").write_text(
            json.dumps({**guard.guarded_units(self.state), unit: "heavy"})
        )
        guard.write_state(
            self.state,
            unit,
            {"deferrals": count, "first_deferred_at": since, "last_reason": "CPU 85 C"},
        )

    def run_notify(self, **kwargs):
        return notify.run(
            self.state,
            notify.CHANNELS,
            now=NOW,
            sender=self.sender,
            environ=ENV,
            **kwargs,
        )


class DroppedTests(NotifyCase):
    def test_quiet_machine_sends_nothing(self) -> None:
        code, report = self.run_notify()
        self.assertEqual((code, self.sent), (0, []))
        self.assertEqual(report, ["nothing to report"])

    def test_several_drops_are_one_message_and_not_repeated(self) -> None:
        self.drop("a.service", NOW - 300)
        self.drop("b.service", NOW - 200)
        code, report = self.run_notify()
        self.assertEqual(code, 0)
        self.assertEqual({s for s, _, _ in self.sent}, {"signal", "gmail"})
        self.assertEqual(len({(subj, body) for _, subj, body in self.sent}), 1)
        self.assertIn("2 dropped", self.sent[0][1])
        self.assertIn("a.service", self.sent[0][2])
        self.assertTrue(any("telegram: no JOB_GUARD_NOTIFY_TELEGRAM_TARGET" in r for r in report))
        self.sent.clear()
        self.run_notify()
        self.assertEqual(self.sent, [])
        self.drop("c.service", NOW - 10)
        self.run_notify()
        self.assertIn("1 dropped", self.sent[0][1])
        self.assertNotIn("a.service", self.sent[0][2])


class PressureTests(NotifyCase):
    def test_short_deferral_is_not_pressure(self) -> None:
        self.defer("a.service", NOW - 600)
        self.run_notify()
        self.assertEqual(self.sent, [])

    def test_sustained_deferral_notifies_once_then_after_the_renotify_window(self) -> None:
        self.defer("a.service", NOW - 3 * 3600)
        self.defer("b.service", NOW - 3 * 3600)
        self.run_notify()
        self.assertEqual(len({subj for _, subj, _ in self.sent}), 1)
        self.assertIn("2 deferring", self.sent[0][1])
        self.sent.clear()
        self.run_notify()
        self.assertEqual(self.sent, [])
        notify.run(
            self.state, notify.CHANNELS, now=NOW + 7 * 3600, sender=self.sender, environ=ENV
        )
        self.assertTrue(self.sent)

    def test_cleared_pressure_is_forgotten_so_the_next_episode_notifies(self) -> None:
        self.defer("a.service", NOW - 3 * 3600)
        self.run_notify()
        guard.write_state(self.state, "a.service", None)
        self.sent.clear()
        self.run_notify()
        self.assertEqual(self.sent, [])
        self.defer("a.service", NOW - 3 * 3600)
        self.run_notify()
        self.assertTrue(self.sent)


class DeliveryTests(NotifyCase):
    def test_one_failing_channel_does_not_stop_the_others(self) -> None:
        self.drop("a.service", NOW - 5)

        def flaky(root, route, subject, body):
            return "boom" if route.service == "signal" else None

        code, report = notify.run(
            self.state, notify.CHANNELS, now=NOW, sender=flaky, environ=ENV
        )
        self.assertEqual(code, 0)
        self.assertIn("signal: FAILED (boom)", report)
        self.assertIn("gmail: sent", report)

    def test_total_failure_keeps_the_event_for_a_retry(self) -> None:
        self.drop("a.service", NOW - 5)
        code, _ = notify.run(
            self.state, notify.CHANNELS, now=NOW, sender=lambda *a: "down", environ=ENV
        )
        self.assertEqual(code, 1)
        self.run_notify()
        self.assertTrue(self.sent)

    def test_no_targets_is_reported_not_marked_sent(self) -> None:
        self.drop("a.service", NOW - 5)
        code, report = notify.run(
            self.state, notify.CHANNELS, now=NOW, sender=self.sender, environ={}
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.sent, [])
        self.assertTrue(any("skipped" in r for r in report))

    def test_dry_run_sends_and_records_nothing(self) -> None:
        self.drop("a.service", NOW - 5)
        code, report = self.run_notify(dry_run=True)
        self.assertEqual((code, self.sent), (0, []))
        self.assertIn("dry run: nothing sent", report)
        self.assertFalse((self.state / notify.STATE_FILE).exists())


class InstallTests(unittest.TestCase):
    def test_notifier_is_never_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unit_dir = Path(tmp)
            for name in ("job-guard-notify", "other"):
                (unit_dir / f"{name}.service").write_text("[Service]\nType=oneshot\n")
                (unit_dir / f"{name}.timer").write_text("[Timer]\nOnCalendar=daily\n")
            self.assertEqual(guard.timer_driven_services(unit_dir), ["other.service"])


if __name__ == "__main__":
    unittest.main()
