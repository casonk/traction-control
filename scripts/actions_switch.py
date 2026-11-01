#!/usr/bin/env python3
"""Turn GitHub Actions on or off across the portfolio, and notice when it is off.

Actions is paused when the monthly minutes run out and has to be switched back on
when the month turns over. Doing that by hand across every repository is slow, and
GitHub separately switches off scheduled workflows after 60 days without activity
(``disabled_inactivity``), which survives a repository-level re-enable. This tool
covers both.

Repositories come from the ignored visibility registry (the control plane's single
list), or from ``--owner`` when no registry is available. Archived repositories are
read-only on GitHub, so they are reported and skipped.

Commands:

- ``status``: repository-level state and any workflow that is not active.
- ``enable``: switch Actions on, then re-enable ``disabled_inactivity`` workflows
  (``--include-manual`` also re-enables ``disabled_manually`` ones, which someone
  may have switched off on purpose).
- ``disable``: switch Actions off and record why, so ``check`` can tell a pause
  you chose from one that just happened. Without ``--repo`` it needs ``--yes``.
- ``usage``: this month's Actions minutes in private repositories (weighted the way
  the allowance is, so a macOS minute counts ten), the free public-repository
  minutes, and any amount actually billed. Needs the ``user`` token scope
  (``gh auth refresh -h github.com -s user``); without it the command says so.
- ``check``: for a timer. Reports repositories left off after the month they were
  disabled in, off with no recorded reason, workflows stuck in
  ``disabled_inactivity``, and minutes use over ``--warn-percent`` of
  ``--limit-minutes``. ``--notify`` sends one message to Signal, Telegram and
  Gmail through shock-relay (targets in ``ACTIONS_SWITCH_NOTIFY_*_TARGET``) and
  repeats it only after ``--renotify-hours``. Check never changes GitHub.

``--dry-run`` on ``enable`` and ``disable`` prints the changes and makes none.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_guard_notify as relay  # noqa: E402
import repository_visibility as visibility  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = REPO_ROOT / "config" / "repository-visibility"
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
DEFAULT_STATE_DIR = STATE_HOME / "portfolio-actions-switch"
STATE_FILE = "state.json"
INACTIVITY = "disabled_inactivity"
MANUAL = "disabled_manually"
DEFAULT_LIMIT_MINUTES = 2000  # GitHub Free
NOTIFY_PREFIX = "ACTIONS_SWITCH_NOTIFY"


class SwitchError(Exception):
    """A failure that should end the command with a message, not a traceback."""


class GitHubError(SwitchError):
    pass


class GitHub:
    """The few GitHub REST calls this tool makes, through the ``gh`` CLI."""

    def __init__(self, gh: str = "gh", timeout: int = 60) -> None:
        self.gh = gh
        self.timeout = timeout

    def api(self, method: str, path: str, fields: dict[str, str] | None = None) -> Any:
        command = [self.gh, "api", "-X", method, path]
        for key, value in (fields or {}).items():
            command += ["-F", f"{key}={value}"]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubError(f"{method} {path}: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise GitHubError(f"{method} {path}: {detail[0] if detail else result.returncode}")
        return json.loads(result.stdout) if result.stdout.strip() else None


@dataclass
class RepoState:
    slug: str
    archived: bool = False
    enabled: bool = False
    workflows: list[tuple[str, str]] = field(default_factory=list)  # (path, state) not active
    error: str | None = None


def observe(github: GitHub, slug: str) -> RepoState:
    state = RepoState(slug)
    try:
        state.archived = bool(github.api("GET", f"repos/{slug}")["archived"])
        state.enabled = bool(github.api("GET", f"repos/{slug}/actions/permissions")["enabled"])
        if state.enabled:
            state.workflows = inactive_workflows(github, slug)
    except (GitHubError, KeyError, TypeError) as exc:
        state.error = str(exc)
    return state


def inactive_workflows(github: GitHub, slug: str) -> list[tuple[str, str]]:
    listing = github.api("GET", f"repos/{slug}/actions/workflows?per_page=100") or {}
    return [
        (w["path"], w["state"]) for w in listing.get("workflows", []) if w["state"] != "active"
    ]


# --- repository selection ---------------------------------------------------


def registry_slugs(
    private: Path, public: Path, local_private: Path | None
) -> tuple[list[str], frozenset[str]]:
    """Every registered slug, and the bare names of the public ones."""
    try:
        pair = visibility.load_pair(private, public, local_private)
    except (visibility.RegistryError, OSError) as exc:
        raise SwitchError(f"cannot read the visibility registry: {exc}") from exc
    slugs = sorted({entry.slug for _, entry in pair.entries}, key=str.casefold)
    public_names = frozenset(e.slug.rsplit("/", 1)[-1].casefold() for e in pair.public.repositories)
    return slugs, public_names


def owner_slugs(github: GitHub, owner: str) -> tuple[list[str], frozenset[str]]:
    listing = github.api("GET", f"users/{owner}/repos?per_page=100&type=owner") or []
    slugs = sorted((r["full_name"] for r in listing), key=str.casefold)
    public = frozenset(r["name"].casefold() for r in listing if not r.get("private", True))
    return slugs, public


def select(universe: Sequence[str], wanted: Sequence[str]) -> list[str]:
    """``wanted`` as slugs or bare names; everything when empty. Unknown names are errors."""
    if not wanted:
        return list(universe)
    by_key = {slug.casefold(): slug for slug in universe}
    by_name = {slug.rsplit("/", 1)[-1].casefold(): slug for slug in universe}
    chosen, unknown = [], []
    for item in wanted:
        slug = by_key.get(item.casefold()) or by_name.get(item.casefold())
        (chosen if slug else unknown).append(slug or item)
    if unknown:
        raise SwitchError(f"not in the repository list: {', '.join(unknown)}")
    return list(dict.fromkeys(chosen))


# --- recorded intent --------------------------------------------------------


def month_of(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def load_ledger(state_dir: Path) -> dict[str, Any]:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads((state_dir / STATE_FILE).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"disabled": dict(data.get("disabled", {})), "notified": dict(data.get("notified", {}))}
    return {"disabled": {}, "notified": {}}


def save_ledger(state_dir: Path, ledger: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, name = tempfile.mkstemp(dir=state_dir, prefix=".state-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(ledger, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(name, 0o600)
        os.replace(name, state_dir / STATE_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


# --- commands ---------------------------------------------------------------


def observe_all(github: GitHub, slugs: Sequence[str]) -> list[RepoState]:
    """Each repository takes three calls, so read them in parallel; order is kept."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda slug: observe(github, slug), slugs))


def render_status(states: Sequence[RepoState], ledger: dict[str, Any]) -> list[str]:
    lines = []
    for s in states:
        if s.error:
            label = f"ERROR  {s.error}"
        elif s.archived:
            label = "archived (read-only)"
        elif not s.enabled:
            note = ledger["disabled"].get(s.slug)
            label = "OFF" + (f"  since {note['at'][:10]}: {note['reason']}" if note else "  (no recorded reason)")
        elif s.workflows:
            label = "on; " + ", ".join(f"{Path(p).name} {w}" for p, w in s.workflows)
        else:
            label = "on"
        lines.append(f"{s.slug:<40} {label}")
    off = sum(1 for s in states if not s.error and not s.archived and not s.enabled)
    stuck = sum(1 for s in states if s.enabled and s.workflows)
    lines.append(f"{len(states)} repositories: {off} off, {stuck} with inactive workflows")
    return lines


def enable(
    github: GitHub,
    slugs: Sequence[str],
    ledger: dict[str, Any],
    *,
    include_manual: bool = False,
    dry_run: bool = False,
) -> tuple[list[str], int]:
    """Returns (report lines, number of failures)."""
    targets = {INACTIVITY, MANUAL} if include_manual else {INACTIVITY}
    report, failures = [], 0
    for slug in slugs:
        state = observe(github, slug)
        if state.error:
            report.append(f"FAIL {slug}: {state.error}")
            failures += 1
            continue
        if state.archived:
            report.append(f"skip {slug}: archived")
            continue
        try:
            if not state.enabled:
                if not dry_run:
                    github.api("PUT", f"repos/{slug}/actions/permissions", {"enabled": "true"})
                report.append(f"{'would enable' if dry_run else 'enabled'} {slug}")
            workflows = state.workflows if state.enabled else (
                [] if dry_run else inactive_workflows(github, slug)
            )
            for path, wstate in workflows:
                if wstate in targets:
                    if not dry_run:
                        github.api("PUT", f"repos/{slug}/actions/workflows/{Path(path).name}/enable")
                    report.append(
                        f"{'would re-enable' if dry_run else 're-enabled'} {slug} {Path(path).name} ({wstate})"
                    )
                else:
                    report.append(f"left {slug} {Path(path).name} ({wstate})")
        except GitHubError as exc:
            report.append(f"FAIL {slug}: {exc}")
            failures += 1
            continue
        if not dry_run:
            ledger["disabled"].pop(slug, None)
    return report or ["nothing to do"], failures


def disable(
    github: GitHub,
    slugs: Sequence[str],
    ledger: dict[str, Any],
    *,
    reason: str,
    now: datetime,
    dry_run: bool = False,
) -> tuple[list[str], int]:
    report, failures = [], 0
    for slug in slugs:
        state = observe(github, slug)
        if state.error:
            report.append(f"FAIL {slug}: {state.error}")
            failures += 1
        elif state.archived:
            report.append(f"skip {slug}: archived")
        elif not state.enabled:
            report.append(f"already off {slug}")
            if not dry_run:
                ledger["disabled"].setdefault(slug, {"at": now.isoformat(), "reason": reason})
        else:
            try:
                if not dry_run:
                    github.api("PUT", f"repos/{slug}/actions/permissions", {"enabled": "false"})
                    ledger["disabled"][slug] = {"at": now.isoformat(), "reason": reason}
                report.append(f"{'would disable' if dry_run else 'disabled'} {slug}")
            except GitHubError as exc:
                report.append(f"FAIL {slug}: {exc}")
                failures += 1
    return report or ["nothing to do"], failures


@dataclass(frozen=True)
class Usage:
    month: str
    minutes: float  # allowance-weighted, private repositories only
    public_minutes: float
    billed: float  # dollars actually charged after the allowance


LINUX_PRICE = 0.006  # dollars per Linux minute; Windows and macOS minutes cost a multiple
PLAN_MINUTES = {"free": 2000, "pro": 3000, "team": 3000, "enterprise": 50000}


def fetch_plan_minutes(github: GitHub) -> float | None:
    """The plan's included private-repository minutes, when GitHub names a plan we know."""
    try:
        return PLAN_MINUTES.get(str((github.api("GET", "user") or {}).get("plan", {}).get("name", "")).casefold())
    except GitHubError:
        return None


def fetch_usage(
    github: GitHub, owner: str, month: str, public_names: frozenset[str] = frozenset()
) -> Usage:
    """Actions minutes for ``month`` (YYYY-MM) from the billing usage report.

    Only private repositories use the included allowance; public ones appear in the
    report at full price and then discount to nothing, so ``public_names`` are counted
    apart. A repository the caller cannot classify counts as private, which errs
    toward warning. A Windows or macOS minute uses several allowance minutes; the
    multiple is its price over the Linux price.
    """
    year, number = month.split("-")
    try:
        report = github.api(
            "GET", f"users/{owner}/settings/billing/usage?year={int(year)}&month={int(number)}"
        )
    except GitHubError as exc:
        # A token without the user scope gets 404 here, so the hint is not conditional on the text.
        raise GitHubError(
            f"billing usage unavailable: {exc} (needs the user scope: gh auth refresh -h github.com -s user)"
        ) from exc
    private = public = billed = 0.0
    for item in (report or {}).get("usageItems", []):
        if str(item.get("product", "")).casefold() != "actions":
            continue
        if str(item.get("unitType", "")).casefold() != "minutes":
            continue
        weight = max(1, round(float(item.get("pricePerUnit") or LINUX_PRICE) / LINUX_PRICE))
        minutes = float(item.get("quantity", 0)) * weight
        billed += float(item.get("netAmount", 0))
        if str(item.get("repositoryName", "")).casefold() in public_names:
            public += minutes
        else:
            private += minutes
    return Usage(month, private, public, billed)


@dataclass(frozen=True)
class Finding:
    key: str
    text: str


def assess(
    states: Sequence[RepoState],
    ledger: dict[str, Any],
    now: datetime,
    usage: Usage | None = None,
    *,
    limit_minutes: float = DEFAULT_LIMIT_MINUTES,
    warn_percent: float = 80,
) -> tuple[list[Finding], list[str]]:
    """What needs attention, and the repositories paused on purpose this month."""
    findings: list[Finding] = []
    paused: list[str] = []
    month = month_of(now)
    for s in states:
        if s.error:
            findings.append(Finding(f"error:{s.slug}", f"{s.slug}: cannot read state ({s.error})"))
        elif s.archived:
            continue
        elif not s.enabled:
            note = ledger["disabled"].get(s.slug)
            if note is None:
                findings.append(Finding(f"unrecorded:{s.slug}", f"{s.slug}: Actions is off with no recorded reason"))
            elif month_of(datetime.fromisoformat(note["at"])) < month:
                findings.append(
                    Finding(
                        f"rolled:{s.slug}",
                        f"{s.slug}: off since {note['at'][:10]} ({note['reason']}); a new month has started",
                    )
                )
            else:
                paused.append(s.slug)
        else:
            for path, wstate in s.workflows:
                if wstate == INACTIVITY:
                    findings.append(
                        Finding(f"stuck:{s.slug}:{path}", f"{s.slug}: {Path(path).name} is {INACTIVITY}")
                    )
    if usage is not None and limit_minutes > 0:
        percent = 100 * usage.minutes / limit_minutes
        if percent >= warn_percent:
            findings.append(
                Finding(
                    f"usage:{usage.month}:{int(percent // 10) * 10}",
                    f"Private-repository Actions minutes {usage.minutes:.0f} of {limit_minutes:.0f} ({percent:.0f}%) in {usage.month}",
                )
            )
    if usage is not None and usage.billed > 0:
        findings.append(
            Finding(f"billed:{usage.month}", f"Actions has been charged ${usage.billed:.2f} in {usage.month}")
        )
    return findings, paused


def compose(findings: Sequence[Finding], total: int, cap: int = 10) -> tuple[str, str]:
    subject = f"GitHub Actions needs attention ({len(findings)})"
    lines = [f.text for f in findings[:cap]]
    if len(findings) > cap:
        lines.append(f"... and {len(findings) - cap} more")
    lines.append(f"Checked {total} repositories. Fix with: scripts/actions_switch.py enable")
    return subject, "\n".join(lines)


def check(
    findings: Sequence[Finding],
    ledger: dict[str, Any],
    state_dir: Path,
    channels: tuple[str, ...],
    total: int,
    *,
    now: float,
    renotify_hours: float,
    notify: bool,
    sender: Callable[[Path, relay.Route, str, str], str | None] = relay.send,
    environ: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Report findings; with ``notify``, send the ones not announced recently."""
    report = [f.text for f in findings] or ["nothing needs attention"]
    notified = ledger["notified"]
    for key in [k for k in notified if k not in {f.key for f in findings}]:
        del notified[key]  # resolved, so a recurrence announces again
    if not notify or not findings:
        if notify:
            save_ledger(state_dir, ledger)
        return (1 if findings else 0), report
    due = [f for f in findings if now - float(notified.get(f.key, 0)) >= renotify_hours * 3600]
    if not due:
        return 1, [*report, "already notified; nothing new to send"]
    subject, body = compose(due, total)
    env = os.environ if environ is None else environ
    routes, delivered = [], 0
    for channel in channels:
        target = env.get(f"{NOTIFY_PREFIX}_{channel.upper()}_TARGET")
        if target:
            routes.append(relay.Route(channel, target, env.get(f"{NOTIFY_PREFIX}_{channel.upper()}_CONFIG")))
        else:
            report.append(f"{channel}: no {NOTIFY_PREFIX}_{channel.upper()}_TARGET, skipped")
    for route in routes:
        error = sender(relay.shock_relay_root(), route, subject, body)
        report.append(f"{route.service}: " + ("sent" if error is None else f"FAILED ({error})"))
        delivered += error is None
    if delivered:
        for f in due:
            notified[f.key] = now
        save_ledger(state_dir, ledger)
        return 1, report
    return 1, [*report, "no channel delivered; will retry"]


# --- command line -----------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, default=REGISTRY_DIR / "private.local.json")
    parser.add_argument("--public", type=Path, default=REGISTRY_DIR / "public.local.json")
    parser.add_argument("--local-private", type=Path, default=REGISTRY_DIR / "local-private.local.json")
    parser.add_argument("--owner", help="List this account's repositories instead of using the registry.")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--gh", default="gh", help="GitHub CLI executable.")
    sub = parser.add_subparsers(dest="command", required=True)

    def repos(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", action="append", default=[], metavar="SLUG", help="Limit to a repository (repeatable).")

    status = sub.add_parser("status", help="Show repository and workflow state.")
    repos(status)
    status.add_argument("--json", action="store_true")
    for name in ("enable", "disable"):
        p = sub.add_parser(name, help=f"Switch Actions {'on' if name == 'enable' else 'off'}.")
        repos(p)
        p.add_argument("--dry-run", action="store_true")
    sub.choices["enable"].add_argument("--include-manual", action="store_true", help="Also re-enable workflows disabled manually.")
    sub.choices["disable"].add_argument("--reason", default="minutes exhausted")
    sub.choices["disable"].add_argument("--yes", action="store_true", help="Required to disable every repository.")
    usage = sub.add_parser("usage", help="Billed Actions minutes for a month.")
    usage.add_argument("--month", help="YYYY-MM (default: this month).")
    chk = sub.add_parser("check", help="Report what needs attention; for a timer.")
    repos(chk)
    chk.add_argument("--notify", action="store_true")
    chk.add_argument("--channels", default="all", help="signal,telegram,gmail or all.")
    chk.add_argument("--renotify-hours", type=float, default=24)
    chk.add_argument("--limit-minutes", type=float, default=float(os.environ["ACTIONS_SWITCH_LIMIT_MINUTES"]) if os.environ.get("ACTIONS_SWITCH_LIMIT_MINUTES") else None, help="Included minutes (default: from your GitHub plan, else 2000).")
    chk.add_argument("--warn-percent", type=float, default=80)
    chk.add_argument("--no-usage", action="store_true", help="Skip the billing lookup.")
    return parser


def _universe(args: argparse.Namespace, github: GitHub) -> tuple[list[str], frozenset[str]]:
    if args.owner:
        return owner_slugs(github, args.owner)
    local = args.local_private if args.local_private.exists() else None
    return registry_slugs(args.private, args.public, local)


def _owner(slugs: Sequence[str], args: argparse.Namespace) -> str:
    return args.owner or (slugs[0].split("/")[0] if slugs else "")


def main(argv: Sequence[str] | None = None, github: GitHub | None = None) -> int:
    args = _parser().parse_args(argv)
    github = github or GitHub(args.gh)
    now = datetime.now(timezone.utc)
    try:
        universe, public_names = _universe(args, github)
        slugs = select(universe, getattr(args, "repo", []))
        ledger = load_ledger(args.state_dir)
        if args.command == "usage":
            month = args.month or month_of(now)
            usage = fetch_usage(github, _owner(universe, args), month, public_names)
            limit = fetch_plan_minutes(github) or DEFAULT_LIMIT_MINUTES
            print(
                f"{usage.month}: {usage.minutes:.0f} private-repository minutes of {limit:.0f} included "
                f"({100 * usage.minutes / limit:.0f}%), {usage.public_minutes:.0f} public (free), "
                f"billed ${usage.billed:.2f}"
            )
            return 0
        if args.command == "status":
            states = observe_all(github, slugs)
            if args.json:
                print(json.dumps([s.__dict__ for s in states], indent=2))
            else:
                print("\n".join(render_status(states, ledger)))
            return 1 if any(s.error for s in states) else 0
        if args.command == "enable":
            report, failures = enable(github, slugs, ledger, include_manual=args.include_manual, dry_run=args.dry_run)
        elif args.command == "disable":
            if not args.repo and not (args.yes or args.dry_run):
                raise SwitchError("disabling every repository needs --yes (or --dry-run to preview)")
            report, failures = disable(github, slugs, ledger, reason=args.reason, now=now, dry_run=args.dry_run)
        else:  # check
            usage = None
            if not args.no_usage:
                with contextlib.suppress(SwitchError):
                    usage = fetch_usage(github, _owner(universe, args), month_of(now), public_names)
            limit = args.limit_minutes or (usage and fetch_plan_minutes(github)) or DEFAULT_LIMIT_MINUTES
            findings, paused = assess(
                observe_all(github, slugs), ledger, now, usage,
                limit_minutes=limit, warn_percent=args.warn_percent,
            )
            names = relay.CHANNELS if args.channels in {"", "all"} else tuple(args.channels.split(","))
            unknown = [c for c in names if c not in relay.SERVICE_SCRIPTS]
            if unknown:
                raise SwitchError(f"unsupported channel(s): {', '.join(unknown)}")
            code, report = check(
                findings, ledger, args.state_dir, names, len(slugs),
                now=time.time(), renotify_hours=args.renotify_hours, notify=args.notify,
            )
            if paused:
                report.append(f"{len(paused)} repositories paused on purpose this month")
            print("\n".join(report))
            return code
        if not getattr(args, "dry_run", False):
            save_ledger(args.state_dir, ledger)
        print("\n".join(report))
        return 1 if failures else 0
    except SwitchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
