#!/usr/bin/env python3
"""Govern the CPU and memory limit of every scheduled job.

Each timer-driven job runs under explicit limits (a CPU quota, a memory ceiling,
and optionally how many other jobs may run beside it). The numbers come from what
the job was actually seen to use, and raising one is a recorded decision, not an
edit:

* ``observe``   per-job peaks from the user journal (systemd logs each run's CPU
                time and memory peak when the unit stops).
* ``baseline``  propose limits for jobs that have none, from those peaks plus
                headroom; ``--write`` records them as the approved starting point.
* ``raise``     the only way to increase a limit. It needs a reason and the
                evidence for it, and appends to the audit log before changing the
                registry. Lowering is free and logged with ``lower``.
* ``check``     fails when a registry value is above its last approved value, a
                job has no approved baseline, or the audit log was edited. Run it
                on a schedule; it also warns about jobs close to, or
                over, their ceiling.
* ``history``   the audit trail of one job.

Both files are local and ignored by Git, because they name real jobs:
``config/resource-limits/limits.local.toml`` (the registry the arbiter reads
through its ``limits_file`` setting) and ``changes.local.jsonl`` (the audit log,
a hash chain so an edit to past entries is detected). They are covered by the
local-state backup like any other ``*.local.*`` config. Synthetic examples are
tracked in ``config/resource-limits/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "config" / "resource-limits"
REGISTRY_NAME = "limits.local.toml"
AUDIT_NAME = "changes.local.jsonl"
UNITS_FILE_ENV = "RESOURCE_LIMITS_UNITS_FILE"

# Limits that loosen when they go up. `nice` is a preference, not a ceiling.
REGULATED = ("cpu_quota", "memory_max", "companions")
FIELDS = (*REGULATED, "nice")
GENESIS = "0" * 64

MEMORY_HEADROOM = 1.5  # ceiling = peak * this ...
MEMORY_STEP_MIB = 128  # ... rounded up to this
MEMORY_FLOOR_MIB = 512
MIN_RUNS = (
    5  # fewer runs than this is thin evidence: the class default is a lower bound
)
CLASS_MEMORY_MIB = {"heavy": 4096, "light": 1024, "exempt": 1024}
CPU_HEADROOM = 3.0  # quota = mean cores of the busiest run * this ...
CPU_STEP = 50  # ... rounded up to this percent
CPU_CEILING = 800
CPU_FLOOR = {"heavy": 200, "light": 100}
WARN_FRACTION = 0.85  # peak this close to the ceiling is worth a look
MIN_RUN_SECONDS = 5.0  # a shorter run says little about CPU rate

_STAMP = re.compile(r"^(\S+)\s")
_STARTING = re.compile(r"Starting (\S+\.service)\b")
_CONSUMED = re.compile(
    r"(\S+\.service): Consumed (.+?) CPU time(?:, ([\d.]+)([KMGT]) memory peak)?"
)
_DURATION = re.compile(r"([\d.]+)\s*(h|min|ms|us|µs|s)\b")


class LimitsError(Exception):
    """A problem the operator has to fix; printed without a traceback."""


# -- reading the journal -----------------------------------------------------


def parse_cpu_seconds(text: str) -> float:
    """CPU time as systemd prints it: ``1h 2min 3.5s``, ``273.410s``, ``812ms``."""
    scale = {"h": 3600.0, "min": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6}
    return sum(float(num) * scale[unit] for num, unit in _DURATION.findall(text))


def parse_mib(number: str, unit: str) -> float:
    return float(number) * {"K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0**2}[unit]


def _epoch(stamp: str) -> float | None:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def observe_lines(
    lines: list[str], units: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Per-unit peaks from journal lines (``-o short-iso``).

    ``mean_cores`` is CPU time over wall time of the busiest run. A run that
    waited in a queue before starting looks idle for longer than it was, which
    only ever lowers the figure; the floors in `derive` keep that from starving
    a job.
    """
    started: dict[str, float] = {}
    seen: dict[str, dict[str, Any]] = {}
    for line in lines:
        stamp = _STAMP.match(line)
        when = _epoch(stamp.group(1)) if stamp else None
        if when is None:
            continue
        if (match := _STARTING.search(line)) and (unit := match.group(1)):
            started[unit] = when
            continue
        if not (match := _CONSUMED.search(line)):
            continue
        unit, cpu_text, number, scale = match.groups()
        if units is not None and unit not in units:
            continue
        cpu = parse_cpu_seconds(cpu_text)
        wall = max(when - started.pop(unit, when), MIN_RUN_SECONDS)
        entry = seen.setdefault(
            unit, {"runs": 0, "peak_mib": 0.0, "max_cpu_s": 0.0, "mean_cores": 0.0}
        )
        entry["runs"] += 1
        if number:
            entry["peak_mib"] = max(entry["peak_mib"], parse_mib(number, scale))
        entry["max_cpu_s"] = max(entry["max_cpu_s"], cpu)
        entry["mean_cores"] = max(entry["mean_cores"], cpu / wall)
    return seen


def journal_lines(days: int) -> list[str]:
    proc = subprocess.run(
        [
            "journalctl",
            "--user",
            "--no-pager",
            "-o",
            "short-iso",
            "--since",
            f"{days} days ago",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise LimitsError(
            f"journalctl failed: {proc.stderr.strip() or proc.returncode}"
        )
    return [
        line
        for line in proc.stdout.splitlines()
        if "Starting " in line or "Consumed " in line
    ]


# -- deriving limits ---------------------------------------------------------


def _round_up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def derive(observed: dict[str, Any] | None, job_class: str) -> dict[str, Any]:
    """Limits for a job from what it was seen to use; class defaults without history."""
    floor = CPU_FLOOR.get(job_class)
    default_mem = CLASS_MEMORY_MIB.get(job_class, 1024)
    limits: dict[str, Any] = {}
    if observed and observed["runs"]:
        mem = max(
            _round_up(observed["peak_mib"] * MEMORY_HEADROOM, MEMORY_STEP_MIB),
            MEMORY_FLOOR_MIB,
        )
        thin = observed["runs"] < MIN_RUNS
        if thin:
            mem = max(mem, default_mem)
        limits["memory_max"] = mem
        if floor:
            cores = _round_up(observed["mean_cores"] * 100 * CPU_HEADROOM, CPU_STEP)
            limits["cpu_quota"] = min(max(cores, floor), CPU_CEILING)
        limits["note"] = (
            f"{observed['runs']} runs: peak {observed['peak_mib']:.0f} MiB, "
            f"max {observed['max_cpu_s']:.0f} CPU-s, busiest {observed['mean_cores']:.2f} cores"
            + (
                f"; under {MIN_RUNS} runs, so not below the class default"
                if thin
                else ""
            )
        )
    else:
        limits["memory_max"] = default_mem
        if floor:
            limits["cpu_quota"] = floor
        limits["note"] = "no history: class default, tighten once it has run"
    return limits


# -- registry and audit log --------------------------------------------------


def registry_path(directory: Path) -> Path:
    return directory / REGISTRY_NAME


def audit_path(directory: Path) -> Path:
    return directory / AUDIT_NAME


def read_registry(directory: Path) -> dict[str, dict[str, Any]]:
    path = registry_path(directory)
    if not path.exists():
        return {}
    try:
        return dict(tomllib.loads(path.read_text(encoding="utf-8")).get("units", {}))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LimitsError(f"{path}: {exc}") from exc


def _toml_value(value: Any) -> str:
    return json.dumps(value) if isinstance(value, str) else str(value)


def write_registry(directory: Path, units: dict[str, dict[str, Any]]) -> None:
    """Replace the registry in one rename, so a reader never sees half of it."""
    lines = [
        "# Resource limits per job. Local, ignored by Git. Managed by",
        "# scripts/resource_limits.py: change it with `raise`/`lower`, not by hand;",
        "# `check` fails on a value above its approved baseline.",
        "",
    ]
    for name in sorted(units):
        lines.append(f'[units."{name}"]')
        for key in (*FIELDS, "note"):
            if key in units[name]:
                lines.append(f"{key} = {_toml_value(units[name][key])}")
        lines.append("")
    directory.mkdir(parents=True, exist_ok=True)
    path = registry_path(directory)
    pending = path.with_suffix(".tmp")
    pending.write_text("\n".join(lines), encoding="utf-8")
    pending.replace(path)


def _digest(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def read_audit(directory: Path) -> list[dict[str, Any]]:
    path = audit_path(directory)
    if not path.exists():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LimitsError(f"{path}:{number}: not JSON ({exc.msg})") from exc
    return entries


def verify_chain(entries: list[dict[str, Any]]) -> str | None:
    """None when the log is intact, else what is wrong with it."""
    previous = GENESIS
    for number, entry in enumerate(entries, 1):
        if entry.get("prev") != previous:
            return (
                f"entry {number} does not follow entry {number - 1}: the log was edited"
            )
        if entry.get("hash") != _digest(entry):
            return f"entry {number} does not match its hash: the log was edited"
        previous = entry["hash"]
    return None


def append_audit(
    directory: Path, entry: dict[str, Any], now: Callable[[], float] = time.time
) -> None:
    entries = read_audit(directory)
    problem = verify_chain(entries)
    if problem:
        raise LimitsError(f"{audit_path(directory)}: {problem}")
    entry = {
        "ts": round(now(), 3),
        **entry,
        "prev": entries[-1]["hash"] if entries else GENESIS,
    }
    entry["hash"] = _digest(entry)
    directory.mkdir(parents=True, exist_ok=True)
    with audit_path(directory).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def approved(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last approved limits of each job, replayed from the log."""
    state: dict[str, dict[str, Any]] = {}
    for entry in entries:
        state.setdefault(entry["unit"], {}).update(entry.get("new", {}))
    return state


def _loosens(key: str, new: Any, old: Any) -> bool:
    """Whether ``new`` lets the job use more than ``old``. A missing limit is unlimited."""
    if old is None:
        return False  # nothing was approved to compare with; `check` reports that apart
    if new is None:
        return True
    return new > old


# -- check -------------------------------------------------------------------


def check(
    registry: dict[str, dict[str, Any]],
    entries: list[dict[str, Any]],
    observed: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """(failures, warnings). Failures are what the regulator refuses."""
    failures: list[str] = []
    warnings: list[str] = []
    problem = verify_chain(entries)
    if problem:
        failures.append(f"audit log: {problem}")
        return failures, warnings
    base = approved(entries)
    for unit, limits in sorted(registry.items()):
        if unit not in base:
            failures.append(
                f"{unit}: has limits but no approved baseline (use baseline --write)"
            )
            continue
        for key in REGULATED:
            if key in limits or key in base[unit]:
                if _loosens(key, limits.get(key), base[unit].get(key)):
                    failures.append(
                        f"{unit}: {key} is {limits.get(key, 'unlimited')}, approved "
                        f"{base[unit].get(key)}; raise it with a reason and evidence"
                    )
    for unit in sorted(set(base) - set(registry)):
        failures.append(f"{unit}: approved but missing from the registry")
    for unit, seen in sorted((observed or {}).items()):
        ceiling = registry.get(unit, {}).get("memory_max")
        if not ceiling:
            continue
        if seen["peak_mib"] >= ceiling:
            warnings.append(
                f"{unit}: peak {seen['peak_mib']:.0f} MiB reached its {ceiling} MiB limit"
            )
        elif seen["peak_mib"] >= ceiling * WARN_FRACTION:
            warnings.append(
                f"{unit}: peak {seen['peak_mib']:.0f} MiB is within 15% of {ceiling} MiB"
            )
    return failures, warnings


# -- commands ----------------------------------------------------------------


def _units_with_classes(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LimitsError(f"cannot read the unit list {path}: {exc}") from exc


def _strip(unit: str) -> str:
    return unit.removesuffix(".service")


def cmd_observe(args: argparse.Namespace, lines: list[str] | None = None) -> int:
    seen = observe_lines(lines if lines is not None else journal_lines(args.days))
    print(
        f"{'unit':<48} {'runs':>5} {'peak MiB':>9} {'max CPU s':>10} {'busiest cores':>14}"
    )
    for unit, row in sorted(seen.items(), key=lambda item: -item[1]["peak_mib"]):
        print(
            f"{unit:<48} {row['runs']:>5} {row['peak_mib']:>9.0f} "
            f"{row['max_cpu_s']:>10.0f} {row['mean_cores']:>14.2f}"
        )
    return 0


def cmd_baseline(args: argparse.Namespace, lines: list[str] | None = None) -> int:
    if args.units_file is None:
        raise LimitsError(f"name the job list with --units-file or ${UNITS_FILE_ENV}")
    classes = _units_with_classes(args.units_file)
    seen = observe_lines(
        lines if lines is not None else journal_lines(args.days), set(classes)
    )
    registry = read_registry(args.dir)
    added: dict[str, dict[str, Any]] = {}
    for unit, job_class in sorted(classes.items()):
        name = _strip(unit)
        if name in registry:
            continue
        added[name] = derive(seen.get(unit), job_class)
    for name, limits in added.items():
        shown = ", ".join(f"{k}={v}" for k, v in limits.items() if k != "note")
        print(f"{name}: {shown}  ({limits['note']})")
    if not added:
        print("every job already has limits")
        return 0
    if not args.write:
        print(f"{len(added)} job(s) would get these limits; add --write to record them")
        return 0
    entries = read_audit(args.dir)
    merged = {**registry, **added}
    for name, limits in added.items():
        append_audit(
            args.dir,
            {
                "unit": name,
                "action": "baseline",
                "new": {k: v for k, v in limits.items() if k in FIELDS},
                "reason": "initial limits from observed history",
                "evidence": limits["note"],
            },
        )
    write_registry(args.dir, merged)
    print(f"recorded {len(added)} baseline(s); audit entries before: {len(entries)}")
    return 0


def _requested(args: argparse.Namespace) -> dict[str, Any]:
    asked = {
        "cpu_quota": args.cpu_quota,
        "memory_max": args.memory_max,
        "companions": args.companions,
        "nice": args.nice,
    }
    return {k: v for k, v in asked.items() if v is not None}


def cmd_change(args: argparse.Namespace) -> int:
    """`raise` and `lower`: change limits, with the log written first."""
    raising = args.command == "raise"
    unit = _strip(args.unit)
    asked = _requested(args)
    if not asked:
        raise LimitsError("name at least one limit to change")
    registry = read_registry(args.dir)
    entries = read_audit(args.dir)
    if problem := verify_chain(entries):
        raise LimitsError(f"audit log: {problem}")
    current = registry.get(unit)
    if current is None:
        raise LimitsError(f"{unit} has no limits yet; run baseline --write first")
    old = {k: current[k] for k in asked if k in current}
    loosening = [
        k for k, v in asked.items() if k in REGULATED and _loosens(k, v, current.get(k))
    ]
    tightening = [k for k, v in asked.items() if k in REGULATED and k not in loosening]
    if raising and not loosening:
        raise LimitsError(
            f"nothing to raise: {', '.join(asked)} would not loosen a limit"
        )
    if not raising and loosening:
        raise LimitsError(
            f"{', '.join(loosening)} would loosen a limit; use raise, with evidence"
        )
    if raising:
        if not (args.reason or "").strip() or not (args.evidence or "").strip():
            raise LimitsError("raising a limit needs --reason and --evidence")
        if tightening:
            raise LimitsError(
                f"do not mix lowering ({', '.join(tightening)}) into a raise"
            )
    append_audit(
        args.dir,
        {
            "unit": unit,
            "action": args.command,
            "old": old,
            "new": asked,
            "reason": (args.reason or "").strip(),
            "evidence": (args.evidence or "").strip(),
            "approved_by": args.approved_by or os.environ.get("USER", "unknown"),
        },
    )
    write_registry(args.dir, {**registry, unit: {**current, **asked}})
    print(
        f"{unit}: {args.command} "
        + ", ".join(f"{k} {old.get(k)} -> {v}" for k, v in asked.items())
    )
    return 0


def cmd_check(args: argparse.Namespace, lines: list[str] | None = None) -> int:
    registry = read_registry(args.dir)
    entries = read_audit(args.dir)
    observed = None
    if not args.no_journal:
        try:
            observed = observe_lines(
                lines if lines is not None else journal_lines(args.days)
            )
        except LimitsError as exc:
            print(f"warning: {exc}; skipping the peak comparison", file=sys.stderr)
    failures, warnings = check(
        registry, entries, {_strip(u): v for u, v in (observed or {}).items()}
    )
    for item in warnings:
        print(f"warning: {item}")
    for item in failures:
        print(f"FAIL: {item}")
    if not failures:
        print(f"ok: {len(registry)} job(s) within their approved limits")
    return 1 if failures else 0


def cmd_history(args: argparse.Namespace) -> int:
    unit = _strip(args.unit)
    rows = [e for e in read_audit(args.dir) if e["unit"] == unit]
    if not rows:
        print(f"no history for {unit}")
        return 0
    for e in rows:
        when = datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M")
        change = ", ".join(
            f"{k} {e.get('old', {}).get(k, '-')} -> {v}" for k, v in e["new"].items()
        )
        print(
            f"{when} {e['action']:<8} {change}\n    why: {e['reason']}\n    evidence: {e['evidence']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Govern per-job CPU and memory limits."
    )
    parser.add_argument(
        "--dir", type=Path, default=DEFAULT_DIR, help="Registry and audit directory."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def with_history(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--days", type=int, default=30, help="Journal days to read (default 30)."
        )

    observe = sub.add_parser("observe", help="Per-job peaks from the journal.")
    with_history(observe)
    baseline = sub.add_parser(
        "baseline", help="Propose (or --write) limits for jobs with none."
    )
    with_history(baseline)
    baseline.add_argument(
        "--units-file",
        type=Path,
        default=Path(os.environ[UNITS_FILE_ENV])
        if os.environ.get(UNITS_FILE_ENV)
        else None,
        help=f"JSON of job name -> class (heavy, light, exempt); default: ${UNITS_FILE_ENV}.",
    )
    baseline.add_argument("--write", action="store_true")
    for name, text in (
        ("raise", "Increase a limit (reason and evidence required)."),
        ("lower", "Decrease a limit."),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("unit")
        p.add_argument("--cpu-quota", type=int, metavar="PERCENT")
        p.add_argument("--memory-max", type=int, metavar="MIB")
        p.add_argument("--companions", type=int, metavar="N")
        p.add_argument("--nice", type=int, metavar="0-19")
        p.add_argument("--reason")
        p.add_argument(
            "--evidence", help="What justifies it: a peak, a run, an incident."
        )
        p.add_argument("--approved-by")
    chk = sub.add_parser("check", help="Fail when a limit is above its approved value.")
    with_history(chk)
    chk.add_argument(
        "--no-journal", action="store_true", help="Skip the peak comparison."
    )
    history = sub.add_parser("history", help="The audit trail of one job.")
    history.add_argument("unit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "observe":
            return cmd_observe(args)
        if args.command == "baseline":
            return cmd_baseline(args)
        if args.command in ("raise", "lower"):
            return cmd_change(args)
        if args.command == "check":
            return cmd_check(args)
        return cmd_history(args)
    except LimitsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
