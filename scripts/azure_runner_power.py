#!/usr/bin/env python3
"""Start the Azure runner VM when CI is waiting for it and deallocate it once it has been idle.

The runner pilot is paid from a free-credit subscription, so the VM should only run while there is
CI to run. ``tick`` (what the timer calls) does both halves:

- **start**: any queued or waiting workflow run in an enrolled repository starts a deallocated VM;
- **stop**: when no enrolled repository has a queued, waiting or in-progress run for ``--idle-minutes``
  the VM is deallocated (compute billing stops; the disk and the static IP are kept, so k3s and the
  runner listeners come back on the next start).

The idle clock lives in the state file, so one quiet poll never stops a VM, and a failed GitHub query
counts as "busy" rather than "idle": an unknown state never powers anything off.

``status`` prints the VM state and the demand without changing anything; ``--dry-run`` on ``tick``
reports what it would do.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
DEFAULT_STATE_DIR = STATE_HOME / "portfolio-runner-pool"
DEFAULT_GROUP = "rg-arc-pilot"
DEFAULT_VM = "arcpilot-vm"
WAITING = ("queued", "waiting", "pending")
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def run(command: list[str]) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def enrolled(state_dir: Path) -> list[str]:
    try:
        data = json.loads((state_dir / "azure-enrolled.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(str(item) for item in data.get("repositories", []))


def vm_state(group: str, vm: str, runner: Runner = run) -> str:
    """``running``, ``deallocated``, ``stopped``, ... or ``unknown`` when Azure could not be asked."""
    out = runner(["az", "vm", "get-instance-view", "-g", group, "-n", vm, "--query",
                  "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]", "-o", "tsv"])  # fmt: skip
    code = out.stdout.strip()
    if out.returncode != 0 or not code.startswith("PowerState/"):
        return "unknown"
    return code.split("/", 1)[1]


STALE_HOURS = 6


def count_runs(slug: str, status: str, runner: Runner = run, since: str = "") -> int | None:
    """Runs in *status* created after *since* (older ones are stuck leftovers, not demand)."""
    query = f"status={status}&per_page=1" + (f"&created=%3E%3D{since}" if since else "")
    out = runner(["gh", "api", f"repos/{slug}/actions/runs?{query}", "--jq", ".total_count"])
    try:
        return int(out.stdout.strip()) if out.returncode == 0 else None
    except ValueError:
        return None


def demand(slugs: Sequence[str], runner: Runner = run, now: datetime | None = None) -> dict:
    """{"waiting": n, "active": n, "unknown": [slugs]} across the enrolled repositories."""
    waiting = active = 0
    unknown: list[str] = []
    since = ((now or datetime.now().astimezone()) - timedelta(hours=STALE_HOURS)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for slug in slugs:
        counts = [count_runs(slug, status, runner, since) for status in (*WAITING, "in_progress")]
        if any(count is None for count in counts):
            unknown.append(slug)
            continue
        waiting += sum(counts[:-1])  # type: ignore[arg-type]
        active += counts[-1]  # type: ignore[assignment]
    return {"waiting": waiting, "active": active, "unknown": unknown}


def load_state(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / "power.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (state_dir / "power.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def decide(power: str, need: dict, state: dict, now: datetime, idle_minutes: int) -> tuple[str, str]:
    """Returns (action, reason); action is start | stop | none. Updates ``state["last_busy"]``."""
    busy = bool(need["waiting"] or need["active"] or need["unknown"])
    if busy:
        state["last_busy"] = now.isoformat(timespec="seconds")
    if power in ("deallocated", "stopped"):
        if need["waiting"]:
            return "start", f"{need['waiting']} run(s) waiting for a runner"
        return "none", f"VM is {power}, nothing waiting"
    if power != "running":
        return "none", f"VM state is {power}; not acting on an unknown state"
    if busy:
        return "none", "CI is active or the state of some repositories is unknown"
    try:
        last = datetime.fromisoformat(state["last_busy"])
    except (KeyError, ValueError):
        state["last_busy"] = now.isoformat(timespec="seconds")  # first sight: start the idle clock
        return "none", "idle clock started"
    if now - last >= timedelta(minutes=idle_minutes):
        return "stop", f"idle for {int((now - last).total_seconds() // 60)} min"
    return "none", f"idle for {int((now - last).total_seconds() // 60)} of {idle_minutes} min"


def cmd_status(args: argparse.Namespace, runner: Runner = run) -> int:
    slugs = enrolled(args.state_dir)
    print(f"vm {args.vm}: {vm_state(args.group, args.vm, runner)}")
    print(f"demand across {len(slugs)} enrolled repositories: {demand(slugs, runner)}")
    return 0


def cmd_tick(args: argparse.Namespace, runner: Runner = run, now: datetime | None = None) -> int:
    now = now or datetime.now().astimezone()
    slugs = enrolled(args.state_dir)
    power = vm_state(args.group, args.vm, runner)
    need = demand(slugs, runner, now)
    state = load_state(args.state_dir)
    action, reason = decide(power, need, state, now, args.idle_minutes)
    print(f"vm={power} waiting={need['waiting']} active={need['active']} unknown={len(need['unknown'])}: {action} ({reason})")
    if action != "none" and not args.dry_run:
        verb = ["start"] if action == "start" else ["deallocate", "--no-wait"]
        result = runner(["az", "vm", *verb, "-g", args.group, "-n", args.vm])
        if result.returncode != 0:
            print(f"az vm {verb[0]} failed: {(result.stderr or result.stdout).strip()[-300:]}", file=sys.stderr)
            return 1
        state[f"last_{action}"] = now.isoformat(timespec="seconds")
        if action == "start":
            state["last_busy"] = now.isoformat(timespec="seconds")
    save_state(args.state_dir, state)
    return 0


def main(argv: Sequence[str] | None = None, runner: Runner = run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--group", default=os.environ.get("AZURE_RUNNER_GROUP", DEFAULT_GROUP))
    parser.add_argument("--vm", default=os.environ.get("AZURE_RUNNER_VM", DEFAULT_VM))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the VM state and CI demand")
    tick = sub.add_parser("tick", help="start the VM if CI is waiting, deallocate it when idle")
    tick.add_argument("--idle-minutes", type=int, default=20)
    tick.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return cmd_status(args, runner) if args.command == "status" else cmd_tick(args, runner)


if __name__ == "__main__":
    sys.exit(main())
