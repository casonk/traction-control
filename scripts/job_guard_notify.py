#!/usr/bin/env python3
"""Tell the owner when the job guard drops a job or the machine stays loaded.

``job_guard.py check`` records an event when it drops a job after its deferral
cap, and keeps per-unit deferral state. This tool, run from a user timer, reads
both and sends at most one message per run:

* **dropped**: jobs the guard gave up on since the last message;
* **pressure**: jobs that have been deferring for ``--pressure-minutes`` or more,
  repeated only after ``--renotify-hours`` while it lasts. When it clears it is
  forgotten, so a new episode notifies again.

One hot evening is therefore one message, not one per job. Messages go to
Signal, Telegram and Gmail through shock-relay's send scripts unless
``--channels`` narrows it. Targets come from the environment:
``JOB_GUARD_NOTIFY_{SIGNAL,TELEGRAM,GMAIL}_TARGET`` (and an optional
``..._CONFIG``); a channel without a target is reported and skipped. State is
committed only when at least one channel delivered, so a failed send is retried.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_guard as guard  # noqa: E402

CHANNELS = ("signal", "telegram", "gmail")
SERVICE_SCRIPTS = {
    "signal": "services/signal-cli/send_message.py",
    "telegram": "services/telegram/send_message.py",
    "gmail": "services/gmail-imap/send_email.py",
}
STATE_FILE = "notify-state.json"


@dataclass(frozen=True)
class Route:
    service: str
    target: str
    config_path: str | None = None


def read_events(state_dir: Path, since: float) -> list[dict]:
    events = []
    with contextlib.suppress(OSError):
        for line in (state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):
                event = json.loads(line)
                if event.get("kind") == "dropped" and float(event.get("t", 0)) > since:
                    events.append(event)
    return events


def pressured(state_dir: Path, now: float, minutes: int) -> list[tuple[str, int, str]]:
    """Units deferring for at least ``minutes``: (unit, deferrals, last reason)."""
    out = []
    for unit in sorted(guard.guarded_units(state_dir)):
        state = guard.read_state(state_dir, unit)
        first = state.get("first_deferred_at")
        if state.get("deferrals") and first and now - float(first) >= minutes * 60:
            out.append((unit, int(state["deferrals"]), str(state.get("last_reason", ""))))
    return out


def load_state(state_dir: Path) -> dict:
    with contextlib.suppress(Exception):
        return json.loads((state_dir / STATE_FILE).read_text(encoding="utf-8"))
    return {}


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / STATE_FILE).write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")


def plan(
    state_dir: Path, now: float, *, pressure_minutes: int, renotify_hours: float
) -> tuple[str | None, str, dict]:
    """The subject (None when quiet), the body, and the state to commit after sending."""
    state = load_state(state_dir)
    dropped = read_events(state_dir, float(state.get("events_through", 0)))
    waiting = pressured(state_dir, now, pressure_minutes)
    notified = float(state.get("pressure_notified_at", 0))
    pressure_due = bool(waiting) and now - notified >= renotify_hours * 3600
    new_state = dict(state)
    if not waiting:
        new_state.pop("pressure_notified_at", None)
    if not dropped and not pressure_due:
        return None, "", new_state
    lines = []
    if dropped:
        lines.append(f"Dropped after the deferral cap ({len(dropped)}):")
        for event in dropped:
            lines.append(f"  {event['unit']}: {event.get('reason', '')}")
        new_state["events_through"] = max(float(e["t"]) for e in dropped)
    if pressure_due:
        lines.append(f"Deferring for {pressure_minutes}+ min ({len(waiting)}):")
        for unit, count, reason in waiting:
            lines.append(f"  {unit}: {count} deferrals, {reason}")
        new_state["pressure_notified_at"] = now
    parts = []
    if dropped:
        parts.append(f"{len(dropped)} dropped")
    if pressure_due:
        parts.append(f"{len(waiting)} deferring")
    return f"Job guard: {', '.join(parts)}", "\n".join(lines), new_state


def routes_from_env(
    channels: tuple[str, ...], environ: dict[str, str] | None = None
) -> tuple[list[Route], list[str]]:
    env = os.environ if environ is None else environ
    routes, missing = [], []
    for channel in channels:
        prefix = f"JOB_GUARD_NOTIFY_{channel.upper()}"
        target = env.get(f"{prefix}_TARGET")
        if target:
            routes.append(Route(channel, target, env.get(f"{prefix}_CONFIG")))
        else:
            missing.append(channel)
    return routes, missing


def shock_relay_root() -> Path:
    if os.environ.get("JOB_GUARD_SHOCK_RELAY_ROOT"):
        return Path(os.environ["JOB_GUARD_SHOCK_RELAY_ROOT"]).expanduser()
    return Path(__file__).resolve().parents[2] / "shock-relay"


def send(root: Path, route: Route, subject: str, body: str) -> str | None:
    """Deliver one message; the error text, or None on success."""
    script = root / SERVICE_SCRIPTS[route.service]
    if not script.exists():
        return f"shock-relay script not found: {script}"
    if route.service == "gmail":
        command = [sys.executable, str(script), route.target, subject, body]
    else:
        command = [sys.executable, str(script), route.target, f"{subject}\n{body}"]
    if route.config_path:
        command += ["--config", route.config_path]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "PYTHONPATH": str(script.parent)},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
    return None


def run(
    state_dir: Path,
    channels: tuple[str, ...],
    *,
    now: float | None = None,
    pressure_minutes: int = 120,
    renotify_hours: float = 6,
    dry_run: bool = False,
    sender: Callable[[Path, Route, str, str], str | None] = send,
    environ: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    now = time.time() if now is None else now
    subject, body, new_state = plan(
        state_dir, now, pressure_minutes=pressure_minutes, renotify_hours=renotify_hours
    )
    if subject is None:  # quiet, possibly ending a pressure episode
        if not dry_run and new_state != load_state(state_dir):
            save_state(state_dir, new_state)
        return 0, ["nothing to report"]
    report = [subject, body]
    if dry_run:
        return 0, [*report, "dry run: nothing sent"]
    routes, missing = routes_from_env(channels, environ)
    for channel in missing:
        report.append(f"{channel}: no JOB_GUARD_NOTIFY_{channel.upper()}_TARGET, skipped")
    delivered = 0
    for route in routes:
        error = sender(shock_relay_root(), route, subject, body)
        report.append(f"{route.service}: " + ("sent" if error is None else f"FAILED ({error})"))
        delivered += error is None
    if delivered:
        save_state(state_dir, new_state)
        return 0, report
    return 1, [*report, "no channel delivered; will retry"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, default=guard.DEFAULT_STATE_DIR)
    parser.add_argument("--channels", default="all", help="signal,telegram,gmail or all.")
    parser.add_argument("--pressure-minutes", type=int, default=120)
    parser.add_argument("--renotify-hours", type=float, default=6)
    parser.add_argument("--dry-run", action="store_true", help="Print the message; send nothing.")
    args = parser.parse_args(argv)
    names = CHANNELS if args.channels in {"", "all"} else tuple(args.channels.split(","))
    unknown = [c for c in names if c not in SERVICE_SCRIPTS]
    if unknown:
        print(f"error: unsupported channel(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    code, report = run(
        args.state_dir,
        names,
        pressure_minutes=args.pressure_minutes,
        renotify_hours=args.renotify_hours,
        dry_run=args.dry_run,
    )
    print("\n".join(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
