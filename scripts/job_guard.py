#!/usr/bin/env python3
"""Keep scheduled jobs from piling onto a machine that is already busy or hot.

Every scheduled job in the portfolio was written as if it ran alone. Each one
is reasonable; together, on one workstation, a handful of them firing in the
same window (renders, audits, a model benchmark) ran the processor to its
thermal limit for hours, and one of them, with no time limit, simply never
finished. Nothing was wrong with any single job. Nothing was looking at the
machine.

This tool is that look. It plugs into systemd rather than into the jobs:

* ``check`` runs as a unit's ``ExecCondition=``. It reads load and
  temperature, sees whether another heavy job is already running, and either
  lets the job start (exit 0) or defers it (exit 75, which systemd records as
  "skipped", not "failed"). A deferred job is retried later through a
  transient timer, a bounded number of times.
* ``install`` writes a drop-in for every timer-driven service so the check and
  a time limit apply without editing the units, the manifests that render
  them, or the jobs themselves. ``install --remove`` takes them out again.
* ``status`` prints the current readings and what each job would do now.

Jobs are **heavy** (the default), **light**, or **exempt**. Heavy jobs wait
for a cool, idle machine and run one at a time. Light jobs are cheap enough to
run alongside anything and wait only when the processor is near its limit.
Exempt jobs are never held.

A sensor that cannot be read never blocks a job. Protection that turns every
missing reading into "do not run" would stop the portfolio on the first
machine without that sensor.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RUN, DEFER = 0, 75  # 75 is EX_TEMPFAIL; any 1-254 makes systemd skip the unit.
DROPIN_NAME = "50-portfolio-job-guard.conf"
CLASSES = ("heavy", "light", "exempt")
_TIMEOUT = re.compile(r"^[1-9][0-9]*(s|min|h)$")
_UNIT = re.compile(r"^[A-Za-z0-9:_.@\\-]+\.service$")
_CPU_ZONE_TYPES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc-thermal")
_CPU_HWMON_NAMES = ("coretemp", "k10temp", "zenpower", "cpu_thermal")

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
DEFAULT_CONFIG = CONFIG_HOME / "portfolio-job-guard" / "config.toml"
DEFAULT_STATE_DIR = STATE_HOME / "portfolio-job-guard"
DEFAULT_UNIT_DIR = CONFIG_HOME / "systemd" / "user"


class GuardError(Exception):
    """Configuration or environment problem the operator has to fix."""


@dataclass(frozen=True)
class Limits:
    load_per_core_busy: float = 0.85
    cpu_temp_hot_c: float = 80.0
    cpu_temp_critical_c: float = 90.0
    gpu_temp_hot_c: float = 80.0
    retry_minutes: int = 20
    retry_jitter_minutes: int = 10
    max_deferrals: int = 6


@dataclass(frozen=True)
class UnitPolicy:
    job_class: str = "heavy"
    timeout: str | None = "3h"


@dataclass(frozen=True)
class Config:
    limits: Limits = field(default_factory=Limits)
    default: UnitPolicy = field(default_factory=UnitPolicy)
    units: dict[str, UnitPolicy] = field(default_factory=dict)

    def policy(self, unit: str) -> UnitPolicy:
        return self.units.get(unit.removesuffix(".service"), self.default)


@dataclass(frozen=True)
class Readings:
    load_per_core: float | None = None
    cpu_temp_c: float | None = None
    gpu_temp_c: float | None = None


@dataclass(frozen=True)
class Peer:
    """Another guarded unit that is currently starting or running."""

    name: str
    started_monotonic: int


# -- configuration -----------------------------------------------------------


def _policy(table: dict, base: UnitPolicy, where: str) -> UnitPolicy:
    job_class = table.get("class", base.job_class)
    if job_class not in CLASSES:
        raise GuardError(f"{where}: class must be one of {', '.join(CLASSES)}")
    timeout = table.get("timeout", base.timeout)
    if timeout in ("", "none", False):
        timeout = None
    if timeout is not None and not (isinstance(timeout, str) and _TIMEOUT.match(timeout)):
        raise GuardError(f"{where}: timeout must look like 90s, 30min or 3h, or be \"none\"")
    return UnitPolicy(job_class=job_class, timeout=timeout)


def load_config(path: Path) -> Config:
    """Read the optional local config. A missing file means built-in defaults."""
    if not path.exists():
        return Config()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise GuardError(f"{path}: {exc}") from exc

    known = Limits.__dataclass_fields__
    raw_limits = document.get("limits", {})
    unknown = sorted(set(raw_limits) - set(known))
    if unknown:
        raise GuardError(f"{path}: unknown limit(s): {', '.join(unknown)}")
    values = {}
    for key, value in raw_limits.items():
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise GuardError(f"{path}: limits.{key} must be a positive number")
        values[key] = type(getattr(Limits(), key))(value)
    limits = Limits(**values)
    if limits.cpu_temp_critical_c < limits.cpu_temp_hot_c:
        raise GuardError(f"{path}: cpu_temp_critical_c must not be below cpu_temp_hot_c")

    default = _policy(document.get("defaults", {}), UnitPolicy(), f"{path}: [defaults]")
    units = {
        name.removesuffix(".service"): _policy(table, default, f"{path}: [units.{name!r}]")
        for name, table in document.get("units", {}).items()
    }
    return Config(limits=limits, default=default, units=units)


# -- readings ----------------------------------------------------------------


def _millidegrees(path: Path) -> float | None:
    with contextlib.suppress(Exception):
        return int(path.read_text(encoding="utf-8").strip()) / 1000.0
    return None


def read_cpu_temp(sys_class: Path = Path("/sys/class")) -> float | None:
    """CPU package temperature, from a sensor that actually names the CPU.

    Never a positional guess: the first thermal zone on a desktop is often a
    board sensor that reads room temperature under full load.
    """
    with contextlib.suppress(Exception):
        for zone in sorted((sys_class / "thermal").glob("thermal_zone*")):
            with contextlib.suppress(Exception):
                if (zone / "type").read_text(encoding="utf-8").strip() in _CPU_ZONE_TYPES:
                    temp = _millidegrees(zone / "temp")
                    if temp is not None:
                        return temp
    with contextlib.suppress(Exception):
        for chip in sorted((sys_class / "hwmon").glob("hwmon*")):
            with contextlib.suppress(Exception):
                if (chip / "name").read_text(encoding="utf-8").strip() in _CPU_HWMON_NAMES:
                    temp = _millidegrees(chip / "temp1_input")
                    if temp is not None:
                        return temp
    return None


def read_gpu_temp() -> float | None:
    """Hottest NVIDIA GPU, or None when there is no nvidia-smi to ask."""
    tool = shutil.which("nvidia-smi")
    if not tool:
        return None
    with contextlib.suppress(Exception):
        out = subprocess.run(
            [tool, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        temps = [float(line) for line in out.stdout.split() if line.strip()]
        if out.returncode == 0 and temps:
            return max(temps)
    return None


def read_readings() -> Readings:
    load = None
    with contextlib.suppress(Exception):
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
    return Readings(load_per_core=load, cpu_temp_c=read_cpu_temp(), gpu_temp_c=read_gpu_temp())


# -- decision ----------------------------------------------------------------


def decide(
    policy: UnitPolicy,
    readings: Readings,
    limits: Limits,
    *,
    earlier_heavy_peers: list[str] | None = None,
) -> tuple[bool, str]:
    """Return (run now?, reason). Pure: every input is passed in."""
    if policy.job_class == "exempt":
        return True, "exempt"

    cpu = readings.cpu_temp_c
    if cpu is not None and cpu >= limits.cpu_temp_critical_c:
        return False, f"CPU {cpu:.0f} C is at or above the {limits.cpu_temp_critical_c:.0f} C limit"
    if policy.job_class == "light":
        return True, "light job, machine below the critical temperature"

    if cpu is not None and cpu >= limits.cpu_temp_hot_c:
        return False, f"CPU {cpu:.0f} C is at or above {limits.cpu_temp_hot_c:.0f} C"
    gpu = readings.gpu_temp_c
    if gpu is not None and gpu >= limits.gpu_temp_hot_c:
        return False, f"GPU {gpu:.0f} C is at or above {limits.gpu_temp_hot_c:.0f} C"
    load = readings.load_per_core
    if load is not None and load >= limits.load_per_core_busy:
        return False, f"load {load:.2f} per core is at or above {limits.load_per_core_busy:.2f}"
    if earlier_heavy_peers:
        return False, f"another heavy job is running: {', '.join(sorted(earlier_heavy_peers))}"
    return True, "machine is cool and idle"


def peers_to_wait_for(unit: str, own_start: int, peers: list[Peer]) -> list[str]:
    """Heavy peers this unit must yield to.

    Two jobs released in the same second would otherwise each see the other
    "running" and both defer, then meet again at the retry. Ordering by start
    time, then by name, means exactly one of them proceeds.
    """
    mine = (own_start, unit)
    return [p.name for p in peers if p.name != unit and (p.started_monotonic, p.name) < mine]


# -- systemd -----------------------------------------------------------------


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("systemctl") or "/usr/bin/systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def active_units(units: list[str]) -> dict[str, int]:
    """Start time (monotonic usec) of each listed unit that is starting or running."""
    if not units:
        return {}
    out = _systemctl(
        "show", "--property=Id,ActiveState,InactiveExitTimestampMonotonic", *units
    ).stdout
    result: dict[str, int] = {}
    for block in out.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("ActiveState") in ("activating", "active"):
            started = props.get("InactiveExitTimestampMonotonic", "0")
            result[props.get("Id", "")] = int(started) if started.isdigit() else 0
    return result


def schedule_retry(unit: str, minutes: int) -> bool:
    tool = shutil.which("systemd-run") or "/usr/bin/systemd-run"
    name = f"job-guard-retry-{unit.removesuffix('.service')}-{int(time.time())}"
    proc = subprocess.run(
        [
            tool,
            "--user",
            "--collect",
            f"--unit={name}",
            f"--description=Retry {unit} after a job-guard deferral",
            f"--on-active={minutes}m",
            shutil.which("systemctl") or "/usr/bin/systemctl",
            "--user",
            "start",
            "--no-block",
            unit,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


# -- state -------------------------------------------------------------------


def _state_path(state_dir: Path, unit: str) -> Path:
    return state_dir / f"{unit.removesuffix('.service')}.json"


def read_state(state_dir: Path, unit: str) -> dict:
    with contextlib.suppress(Exception):
        return json.loads(_state_path(state_dir, unit).read_text(encoding="utf-8"))
    return {}


def write_state(state_dir: Path, unit: str, state: dict | None) -> None:
    path = _state_path(state_dir, unit)
    if state is None:
        path.unlink(missing_ok=True)
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")


def guarded_units(state_dir: Path) -> dict[str, str]:
    """Unit name -> class, as recorded by the last ``install``."""
    with contextlib.suppress(Exception):
        return json.loads((state_dir / "guarded-units.json").read_text(encoding="utf-8"))
    return {}


# -- commands ----------------------------------------------------------------


def check(
    unit: str,
    config: Config,
    state_dir: Path,
    *,
    readings: Readings | None = None,
    active: dict[str, int] | None = None,
    retry=schedule_retry,
    now: float | None = None,
    rng: random.Random | None = None,
) -> tuple[int, str]:
    """Decide for one unit, record the outcome, and schedule a retry on deferral."""
    policy = config.policy(unit)
    readings = read_readings() if readings is None else readings
    waiting_for: list[str] = []
    if policy.job_class == "heavy":
        heavy = [u for u, c in guarded_units(state_dir).items() if c == "heavy"]
        if active is None:
            active = active_units(sorted(set(heavy) | {unit}))
        peers = [Peer(name, started) for name, started in active.items() if name in heavy]
        waiting_for = peers_to_wait_for(unit, active.get(unit, 0), peers)

    run, reason = decide(policy, readings, config.limits, earlier_heavy_peers=waiting_for)
    if run:
        write_state(state_dir, unit, None)
        return RUN, f"{unit}: starting ({reason})"

    state = read_state(state_dir, unit)
    count = int(state.get("deferrals", 0)) + 1
    if count > config.limits.max_deferrals:
        # Stop chasing this trigger; the unit's own timer will fire again on
        # schedule. Running anyway would defeat the point of the guard.
        write_state(state_dir, unit, None)
        return DEFER, (
            f"{unit}: skipped after {config.limits.max_deferrals} deferrals ({reason}); "
            "waiting for its next scheduled run"
        )
    rng = rng or random.Random()
    minutes = config.limits.retry_minutes + rng.randint(0, config.limits.retry_jitter_minutes)
    scheduled = retry(unit, minutes)
    write_state(
        state_dir,
        unit,
        {
            "deferrals": count,
            "first_deferred_at": state.get("first_deferred_at", now or time.time()),
            "last_reason": reason,
        },
    )
    tail = f"retry in {minutes} min" if scheduled else "could not schedule a retry"
    return DEFER, f"{unit}: deferred {count}/{config.limits.max_deferrals} ({reason}); {tail}"


def timer_driven_services(unit_dir: Path) -> list[str]:
    """Services in ``unit_dir`` that a timer in the same directory activates."""
    services = set()
    for timer in sorted(unit_dir.glob("*.timer")):
        target = None
        for line in timer.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("Unit="):
                target = line.split("=", 1)[1].strip()
        target = target or timer.name.removesuffix(".timer") + ".service"
        template = re.sub(r"@[^.]*\.service$", "@.service", target)
        if _UNIT.match(target) and ((unit_dir / target).exists() or (unit_dir / template).exists()):
            services.add(target)
    return sorted(services)


def _unit_text(unit_dir: Path, unit: str) -> str:
    """The unit file's text, falling back to its template for an instance."""
    template = re.sub(r"@[^.]*\.service$", "@.service", unit)
    for candidate in (unit_dir / unit, unit_dir / template):
        with contextlib.suppress(OSError):
            return candidate.read_text(encoding="utf-8", errors="replace")
    return ""


def _declares_timeout(unit_dir: Path, unit: str) -> bool:
    return bool(
        re.search(r"^\s*(TimeoutStartSec|RuntimeMaxSec)\s*=", _unit_text(unit_dir, unit), re.M)
    )


def _declares_guard(unit_dir: Path, unit: str) -> bool:
    """True when the unit already carries this guard, e.g. rendered by clockwork."""
    return bool(
        re.search(
            r"^\s*ExecCondition\s*=.*\bjob_guard\.py\s+check\b",
            _unit_text(unit_dir, unit),
            re.M,
        )
    )


def _is_oneshot(unit_dir: Path, unit: str) -> bool:
    types = re.findall(r"^\s*Type\s*=\s*(\S+)", _unit_text(unit_dir, unit), re.M)
    return bool(types) and types[-1] == "oneshot"


def render_dropin(
    unit: str,
    policy: UnitPolicy,
    script: Path,
    *,
    keep_own_timeout: bool,
    oneshot: bool = True,
    keep_own_condition: bool = False,
) -> str:
    lines = [
        "# Managed by traction-control scripts/job_guard.py; do not edit.",
        "# Remove every such drop-in with: job_guard.py install --remove",
        "[Service]",
    ]
    if policy.job_class != "exempt" and not keep_own_condition:
        lines.append(f"ExecCondition=/usr/bin/python3 {script} check --unit %n")
    if policy.timeout and not keep_own_timeout:
        # A oneshot job is "starting" for as long as it runs, so TimeoutStartSec
        # bounds it and RuntimeMaxSec does nothing (systemd warns about it on
        # every reload). Any other type is bounded by RuntimeMaxSec instead.
        key = "TimeoutStartSec" if oneshot else "RuntimeMaxSec"
        lines.append(f"{key}={policy.timeout}")
    return "\n".join(lines) + "\n"


def install(
    unit_dir: Path, config: Config, state_dir: Path, script: Path, *, remove: bool, dry_run: bool
) -> list[str]:
    """Write (or remove) the drop-in for every timer-driven service."""
    report: list[str] = []
    if remove:
        for dropin in sorted(unit_dir.glob(f"*.service.d/{DROPIN_NAME}")):
            report.append(f"remove {dropin.parent.name}/{DROPIN_NAME}")
            if not dry_run:
                dropin.unlink()
                with contextlib.suppress(OSError):
                    dropin.parent.rmdir()
        if not dry_run:
            (state_dir / "guarded-units.json").unlink(missing_ok=True)
        return report

    recorded: dict[str, str] = {}
    for unit in timer_driven_services(unit_dir):
        policy = config.policy(unit)
        own = _declares_timeout(unit_dir, unit)
        native = _declares_guard(unit_dir, unit)
        body = render_dropin(
            unit,
            policy,
            script,
            keep_own_timeout=own,
            oneshot=_is_oneshot(unit_dir, unit),
            keep_own_condition=native,
        )
        limit = "own limit kept" if own else (policy.timeout or "no limit")
        report.append(
            f"{unit}: {policy.job_class}, {limit}" + (", own check kept" if native else "")
        )
        recorded[unit] = policy.job_class
        if dry_run:
            continue
        target = unit_dir / f"{unit}.d"
        target.mkdir(parents=True, exist_ok=True)
        (target / DROPIN_NAME).write_text(body, encoding="utf-8")
    if not dry_run:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "guarded-units.json").write_text(
            json.dumps(recorded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report


def _fmt(value: float | None, unit: str, digits: int = 0) -> str:
    return "unknown" if value is None else f"{value:.{digits}f}{unit}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="ExecCondition entry point for one unit.")
    p_check.add_argument("--unit", required=True)

    p_install = sub.add_parser("install", help="Write the drop-in for timer-driven services.")
    p_install.add_argument("--unit-dir", type=Path, default=DEFAULT_UNIT_DIR)
    p_install.add_argument("--remove", action="store_true", help="Remove the drop-ins instead.")
    p_install.add_argument("--dry-run", action="store_true")
    p_install.add_argument("--no-reload", action="store_true", help="Skip systemctl daemon-reload.")

    sub.add_parser("status", help="Show readings and what each guarded job would do now.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "check":
            if not _UNIT.match(args.unit):
                raise GuardError(f"not a service unit name: {args.unit!r}")
            code, message = check(args.unit, config, args.state_dir)
            print(message)
            return code
        if args.command == "install":
            script = Path(__file__).resolve()
            for line in install(
                args.unit_dir,
                config,
                args.state_dir,
                script,
                remove=args.remove,
                dry_run=args.dry_run,
            ):
                print(("would " if args.dry_run else "") + line)
            if not args.dry_run and not args.no_reload:
                _systemctl("daemon-reload")
            return 0
        readings = read_readings()
        print(
            f"load {_fmt(readings.load_per_core, ' per core', 2)}, "
            f"CPU {_fmt(readings.cpu_temp_c, ' C')}, GPU {_fmt(readings.gpu_temp_c, ' C')}"
        )
        units = guarded_units(args.state_dir)
        active = active_units(sorted(units))
        for unit, job_class in sorted(units.items()):
            heavy_peers = [u for u in active if units.get(u) == "heavy" and u != unit]
            run, reason = decide(
                config.policy(unit),
                readings,
                config.limits,
                earlier_heavy_peers=heavy_peers if job_class == "heavy" else None,
            )
            state = read_state(args.state_dir, unit)
            waiting = f" [deferred {state['deferrals']}x]" if state.get("deferrals") else ""
            running = " [running]" if unit in active else ""
            print(f"  {'run  ' if run else 'defer'} {unit} ({job_class}){running}{waiting}: {reason}")
        if not units:
            print("  no guarded units recorded; run: job_guard.py install")
        return 0
    except GuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.command == "check":
            # Fail open. A typo in the guard's config must not stop every
            # scheduled job on the machine; the error is in the journal.
            print(f"{args.unit}: starting unguarded because the guard could not run")
            return RUN
        return 2
    except Exception as exc:  # noqa: BLE001 - last line of defence for `check`
        if args.command != "check":
            raise
        print(f"error: job guard failed unexpectedly: {exc!r}", file=sys.stderr)
        print(f"{args.unit}: starting unguarded because the guard could not run")
        return RUN


if __name__ == "__main__":
    raise SystemExit(main())
