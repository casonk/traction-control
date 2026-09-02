"""Cross-repository regression for the Clockwork/traction launchd contract."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_ROOT = REPO_ROOT.parent.parent
CLOCKWORK_ROOT = REPO_ROOT.parent / "clockwork"
INSTALLER = REPO_ROOT / "scripts" / "install_traction_control_agents.sh"
JOBS_CONFIG = REPO_ROOT / "config" / "traction-control-agents" / "jobs.conf"


def _job_records() -> dict[str, dict[str, str]]:
    fields = (
        "profiles",
        "name",
        "command",
        "arguments",
        "linux_installer",
        "installer_kind",
        "provider_env",
        "model_env",
        "env_slug",
        "schedule_kind",
        "interval_seconds",
        "weekdays",
        "hour",
        "minute",
        "startup_delay",
        "jitter",
        "network_host",
        "activation_kind",
    )
    records = {}
    for source in JOBS_CONFIG.read_text(encoding="utf-8").splitlines():
        if not source or source.startswith("#"):
            continue
        values = source.split("|")
        if len(values) != len(fields):
            raise AssertionError(f"unexpected jobs.conf record width: {source}")
        record = dict(zip(fields, values, strict=True))
        records[record["name"]] = record
    return records


def _clockwork_jobs() -> dict[str, dict]:
    jobs = {}
    for path in sorted(
        (CLOCKWORK_ROOT / "examples" / "traction-control").glob("*.toml")
    ):
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        for job in document.get("jobs", []):
            jobs[job["name"]] = job
    return jobs


def _ui_labels(jobs: dict[str, dict]) -> dict[str, str]:
    sys.path.insert(0, os.fspath(CLOCKWORK_ROOT / "src"))
    try:
        from clockwork.render import resolve_launchd_label
    finally:
        sys.path.pop(0)

    return {
        name: resolve_launchd_label(name, job.get("launchd_label"))
        for name, job in jobs.items()
    }


def _runtime_arguments(payload: dict) -> list[str]:
    arguments = payload["ProgramArguments"]
    if arguments[:2] == ["/usr/bin/python3", "-c"]:
        return json.loads(arguments[3])["argv"]
    return arguments


def _duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd]?)", value)
    if match is None:
        raise AssertionError(f"unsupported duration in Clockwork manifest: {value}")
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(match.group(1)) * multiplier[match.group(2)]


def _calendar_parts(value: str) -> tuple[set[int], int, int]:
    match = re.fullmatch(
        r"(?:(?P<weekdays>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
        r"(?:,(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))*) )?"
        r"\*-\*-\* (\d{2}):(\d{2}):\d{2}",
        value,
    )
    if match is None:
        raise AssertionError(f"unsupported calendar in Clockwork manifest: {value}")
    weekday_numbers = {
        "Sun": 0,
        "Mon": 1,
        "Tue": 2,
        "Wed": 3,
        "Thu": 4,
        "Fri": 5,
        "Sat": 6,
    }
    weekdays = (
        {weekday_numbers[name] for name in match.group("weekdays").split(",")}
        if match.group("weekdays")
        else set()
    )
    return weekdays, int(match.group(2)), int(match.group(3))


@unittest.skipUnless(
    (CLOCKWORK_ROOT / "src" / "clockwork" / "render.py").is_file(),
    "sibling Clockwork checkout is required for the cross-repo contract test",
)
class ClockworkLaunchdContractTests(unittest.TestCase):
    maxDiff = None

    def _render(self, root: Path, *, autonomous: bool) -> dict[str, dict]:
        state_dir = root / ("state-autonomous" if autonomous else "state-normal")
        launchd_dir = root / ("launchd-autonomous" if autonomous else "launchd-normal")
        home = root / "home"
        home.mkdir(exist_ok=True)
        command = [
            "/bin/bash",
            os.fspath(INSTALLER),
            "--tier",
            "heavy",
            "--portfolio-root",
            os.fspath(PORTFOLIO_ROOT),
            "--platform",
            "macos",
            "--provider",
            "auto",
            "--no-clone",
            "--state-dir",
            os.fspath(state_dir),
            "--launchd-dir",
            os.fspath(launchd_dir),
        ]
        if autonomous:
            command.append("--enable-autonomous-ci-repair")
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": os.fspath(home),
                "XDG_CONFIG_HOME": os.fspath(home / ".config"),
                "XDG_DATA_HOME": os.fspath(home / ".local" / "share"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        if shutil.which("plutil", path=environment.get("PATH")) is None:
            fake_bin = root / "fake-bin"
            fake_bin.mkdir(exist_ok=True)
            fake_plutil = fake_bin / "plutil"
            fake_plutil.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "if [[ \"${1:-}\" == \"-lint\" ]]; then\n"
                "  shift\n"
                "  for path in \"$@\"; do\n"
                "    [[ -f \"$path\" ]] || exit 1\n"
                "  done\n"
                "  exit 0\n"
                "fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_plutil.chmod(0o755)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payloads = {}
        for path in sorted(launchd_dir.glob("*.plist")):
            payload = plistlib.loads(path.read_bytes())
            payloads[payload["Label"].rsplit(".", 1)[-1]] = payload
        return payloads

    def test_ui_and_tier_installer_compose_exact_labels_and_explicit_adapters(self):
        records = _job_records()
        clockwork_jobs = _clockwork_jobs()
        ui_labels = _ui_labels(clockwork_jobs)
        self.assertEqual(set(ui_labels), set(records))

        with tempfile.TemporaryDirectory(
            prefix="clockwork-traction-contract."
        ) as temporary:
            root = Path(temporary)
            payloads = self._render(root, autonomous=False)
            payloads.update(self._render(root, autonomous=True))

        rendered_labels = {name: payload["Label"] for name, payload in payloads.items()}
        self.assertEqual(rendered_labels, ui_labels)
        self.assertEqual(len(set(rendered_labels.values())), len(rendered_labels))

        for name, record in records.items():
            payload = payloads[name]
            arguments = _runtime_arguments(payload)
            self.assertNotIn("--env-file", arguments)
            if record["env_slug"] == "-":
                self.assertNotEqual(
                    payload["ProgramArguments"][:2], ["/usr/bin/python3", "-c"]
                )
            else:
                self.assertEqual(
                    payload["ProgramArguments"][:2], ["/usr/bin/python3", "-c"]
                )
                loader = json.loads(payload["ProgramArguments"][3])
                self.assertEqual(
                    len(loader["environment_files"]),
                    1,
                )
                self.assertTrue(
                    loader["environment_files"][0].endswith(
                        f"/.config/traction-control/{record['env_slug']}.env"
                    )
                )
                self.assertTrue(loader["environment_files"][0].startswith("-"))
            self.assertEqual(
                arguments[arguments.index("--schedule-kind") + 1],
                record["schedule_kind"],
            )
            self.assertEqual(
                arguments[arguments.index("--jitter-seconds") + 1],
                record["jitter"],
            )
            self.assertEqual(
                arguments[arguments.index("--network-host") + 1],
                record["network_host"],
            )
            self.assertEqual(
                arguments[arguments.index("--network-wait-seconds") + 1], "300"
            )
            if record["schedule_kind"] == "interval":
                self.assertEqual(
                    arguments[arguments.index("--startup-delay-seconds") + 1],
                    record["startup_delay"],
                )
                self.assertEqual(
                    arguments[arguments.index("--interval-seconds") + 1],
                    record["interval_seconds"],
                )
                self.assertEqual(
                    arguments[arguments.index("--retry-seconds") + 1], "300"
                )
                self.assertIs(payload["RunAtLoad"], True)
                self.assertEqual(payload["StartInterval"], 300)
            else:
                self.assertNotIn("--startup-delay-seconds", arguments)
                self.assertNotIn("--interval-seconds", arguments)
                self.assertIs(payload["RunAtLoad"], False)
            if record["schedule_kind"] == "calendar":
                intervals = payload["StartCalendarInterval"]
                if isinstance(intervals, dict):
                    intervals = [intervals]
                expected_weekdays = (
                    [int(value) for value in record["weekdays"].split(",")]
                    if record["weekdays"] != "-"
                    else []
                )
                self.assertEqual(
                    [
                        interval["Weekday"]
                        for interval in intervals
                        if "Weekday" in interval
                    ],
                    expected_weekdays,
                )
                self.assertEqual(
                    {interval["Hour"] for interval in intervals},
                    {int(record["hour"])},
                )
                self.assertEqual(
                    {interval["Minute"] for interval in intervals},
                    {int(record["minute"])},
                )
            elif record["schedule_kind"] == "none":
                self.assertNotIn("StartInterval", payload)
                self.assertNotIn("StartCalendarInterval", payload)

            clockwork_timer = clockwork_jobs[name].get("timer")
            if record["schedule_kind"] == "interval":
                self.assertEqual(clockwork_timer["kind"], "interval")
                self.assertEqual(
                    _duration_seconds(clockwork_timer["on_unit_active_sec"]),
                    int(record["interval_seconds"]),
                )
            elif record["schedule_kind"] == "calendar":
                self.assertEqual(clockwork_timer["kind"], "calendar")
                weekdays, hour, minute = _calendar_parts(clockwork_timer["on_calendar"])
                expected_weekdays = (
                    {int(value) for value in record["weekdays"].split(",")}
                    if record["weekdays"] != "-"
                    else set()
                )
                self.assertEqual(weekdays, expected_weekdays)
                self.assertEqual(hour, int(record["hour"]))
                self.assertEqual(minute, int(record["minute"]))
            else:
                self.assertIsNone(clockwork_timer)


if __name__ == "__main__":
    unittest.main()
