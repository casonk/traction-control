#!/usr/bin/env python3
"""Monitor Gmail for Codecov patch-coverage comments on pull requests.

Codecov reaches the inbox as a GitHub pull-request comment notification from
``codecov[bot]``. When a comment reports that a pull request's patch coverage
is short, this monitor

* records a backlog item in the repository's ``BACKLOG.md`` (through a
  temporary worktree of ``origin/main`` and a pull request, never in a live
  checkout and never merged), and
* for a pull request that is still open, writes a candidate file and schedules
  a delayed agentic repair that adds tests on the pull request branch.

The mailbox is only read. The operator already receives these emails, so the
monitor sends no notification of its own and never moves or marks a message.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monitor_github_ci_emails as ci  # noqa: E402  (shared Gmail, state and repo-index helpers)

MonitorError = ci.MonitorError

REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_STATE_FILE = (
    Path.home() / ".local" / "share" / "traction-control" / "codecov-email-monitor.json"
)
DEFAULT_CANDIDATE_FILE = (
    Path.home() / ".local" / "share" / "traction-control" / "codecov-candidates.tsv"
)
DEFAULT_FROM_FILTER = "notifications@github.com"
DEFAULT_SINCE_DAYS = 3
DEFAULT_LIMIT = 200
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_TRIGGER_DELAY_MINUTES = 15
DEFAULT_TRIGGER_SERVICE = "codecov-repair-agentic.service"
DEFAULT_TRIGGER_UNIT = "codecov-repair-agentic-email-trigger"
BACKLOG_MARKER = "codecov-pr"
CANDIDATE_HEADER = (
    "status",
    "repo_slug",
    "repo_rel",
    "pr",
    "branch",
    "patch_pct",
    "missing_lines",
    "files",
    "comment_url",
    "detected_at",
)

COMMENT_RE = re.compile(r"codecov\[bot\]\s+left a comment\s+\((?P<slug>[^\s#()]+)#(?P<pr>\d+)\)")
PATCH_RE = re.compile(
    r"Patch coverage is\s+`?(?P<pct>\d+(?:\.\d+)?)%`?\s+with\s+`?(?P<missing>\d+)\s+lines?`?"
)
ATTENTION_RE = re.compile(
    r"Attention:\s*`?(?P<missing>\d+)\s+lines?`?\s+in your changes (?:are|is) missing coverage"
)
FILE_ROW_RE = re.compile(
    r"^\|\s*\[(?P<path>[^\]]+)\]\([^)]*\)\s*\|\s*(?P<pct>\d+(?:\.\d+)?)%\s*\|"
    r"\s*\[?(?P<missing>\d+)\s+Missing",
    re.MULTILINE,
)
COMMENT_URL_RE = re.compile(
    r"https://github\.com/(?P<slug>[^/\s]+/[^/\s#]+)/pull/(?P<pr>\d+)#issuecomment-(?P<id>\d+)"
)
FAIL_MARKERS = (":x:", "❌")


@dataclass(frozen=True)
class CodecovFile:
    path: str
    patch_pct: float
    missing: int


@dataclass(frozen=True)
class CodecovComment:
    message_id: str
    received_at: str
    repo_slug: str
    repo_rel: str | None
    pr: int
    comment_id: str | None
    comment_url: str
    patch_pct: float | None
    missing_lines: int
    failed: bool
    files: tuple[CodecovFile, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MonitorConfig:
    portfolio_root: Path
    shock_relay_root: Path
    gmail_config: Path
    state_file: Path
    candidate_file: Path
    mailbox: str
    from_filter: str
    since_days: int
    limit: int
    min_patch_pct: float | None
    max_attempts: int
    messages_file: Path | None
    add_backlog: bool
    trigger_repair: bool
    trigger_delay_minutes: int
    trigger_service: str
    trigger_unit: str
    dry_run: bool
    output_json: bool


# --------------------------------------------------------------------------- parsing


def _body_text(message: dict[str, Any]) -> str:
    parts = (message.get("text"), message.get("html"), message.get("snippet"))
    return "\n".join(str(part) for part in parts if part)


def parse_codecov_comment(
    message: dict[str, Any], repo_index: dict[str, str]
) -> CodecovComment | None:
    """Parse one message; return None unless it is a Codecov report comment."""
    body = _body_text(message)
    header = COMMENT_RE.search(body)
    if not header or "Codecov" not in body:
        return None
    slug = header.group("slug")
    pr = int(header.group("pr"))

    patch = PATCH_RE.search(body)
    attention = ATTENTION_RE.search(body)
    patch_pct: float | None = None
    missing = 0
    if patch:
        patch_pct = float(patch.group("pct"))
        missing = int(patch.group("missing"))
    elif attention:
        missing = int(attention.group("missing"))

    failed = False
    for line in body.splitlines():
        if ("Patch coverage" in line or "Attention:" in line) and any(
            marker in line for marker in FAIL_MARKERS
        ):
            failed = True
            break
    if not failed and attention:
        failed = True

    files = tuple(
        CodecovFile(
            path=row.group("path").replace("\\_", "_").replace("\\", ""),
            patch_pct=float(row.group("pct")),
            missing=int(row.group("missing")),
        )
        for row in FILE_ROW_RE.finditer(body)
    )

    url_match = COMMENT_URL_RE.search(body)
    comment_id = url_match.group("id") if url_match else None
    comment_url = (
        url_match.group(0) if url_match else f"https://github.com/{slug}/pull/{pr}"
    )
    message_id = str(message.get("message_id") or "").strip()
    return CodecovComment(
        message_id=message_id or f"<codecov-{slug}-{pr}-{comment_id or 'unknown'}>",
        received_at=str(message.get("date") or "").strip(),
        repo_slug=slug,
        repo_rel=repo_index.get(slug),
        pr=pr,
        comment_id=comment_id,
        comment_url=comment_url,
        patch_pct=patch_pct,
        missing_lines=missing,
        failed=failed,
        files=files,
    )


def needs_action(comment: CodecovComment, min_patch_pct: float | None) -> bool:
    """A comment needs action when Codecov flagged the patch, or it is under the floor."""
    if comment.missing_lines <= 0:
        return False
    if comment.failed:
        return True
    return (
        min_patch_pct is not None
        and comment.patch_pct is not None
        and comment.patch_pct < min_patch_pct
    )


def comment_key(comment: CodecovComment) -> str:
    return f"{comment.repo_slug}#{comment.pr}"


def describe_files(files: Iterable[CodecovFile], limit: int = 5) -> str:
    items = list(files)
    shown = ", ".join(f"{f.path} ({f.missing} missing, {f.patch_pct:g}%)" for f in items[:limit])
    extra = len(items) - limit
    return shown + (f", and {extra} more" if extra > 0 else "")


def backlog_line(comment: CodecovComment, today: str) -> str:
    pct = f"{comment.patch_pct:g}% patched, " if comment.patch_pct is not None else ""
    files = describe_files(comment.files)
    detail = f" Files: {files}." if files else ""
    return (
        f"- [ ] [codecov:{today}] **Raise patch coverage on PR #{comment.pr}: "
        f"{pct}{comment.missing_lines} changed lines have no test coverage.**{detail} "
        f"Add tests for the uncovered lines. "
        f"({BACKLOG_MARKER}:{comment.pr}, {comment.comment_url})"
    )


def insert_pending_item(backlog_text: str, line: str, marker: str) -> str | None:
    """Return the backlog text with ``line`` under ``## Pending``; None if already present."""
    if marker in backlog_text:
        return None
    lines = backlog_text.splitlines()
    for index, text in enumerate(lines):
        if text.strip().lower() == "## pending":
            at = index + 1
            if at >= len(lines) or lines[at].strip() != "":
                lines.insert(at, "")
            lines.insert(at + 1, line)
            break
    else:
        lines.extend(["", "## Pending", "", line])
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- side effects


Runner = Callable[[list[str], Path | None], "subprocess.CompletedProcess[str]"]


def run_command(args: list[str], cwd: Path | None = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False)


def pull_request_state(slug: str, pr: int, runner: Runner = run_command) -> dict[str, Any] | None:
    result = runner(
        ["gh", "pr", "view", str(pr), "-R", slug, "--json", "state,headRefName,isCrossRepository"],
        None,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def add_backlog_item(
    comment: CodecovComment,
    repo_dir: Path,
    today: str,
    runner: Runner = run_command,
) -> tuple[str, str]:
    """Open a pull request that records the item. Returns (outcome, detail)."""
    marker = f"{BACKLOG_MARKER}:{comment.pr}"
    line = backlog_line(comment, today)
    runner(["git", "fetch", "-q", "origin", "main"], repo_dir)
    shown = runner(["git", "show", "origin/main:BACKLOG.md"], repo_dir)
    if shown.returncode != 0:
        return "skipped", "BACKLOG.md is not on origin/main"
    updated = insert_pending_item(shown.stdout, line, marker)
    if updated is None:
        return "exists", marker

    branch = f"chore/codecov-backlog-pr{comment.pr}"
    with tempfile.TemporaryDirectory(prefix="codecov-backlog-") as tmp:
        worktree = Path(tmp) / "wt"
        added = runner(
            ["git", "worktree", "add", "-q", "-B", branch, str(worktree), "origin/main"], repo_dir
        )
        if added.returncode != 0:
            return "failed", added.stderr.strip() or "git worktree add failed"
        try:
            (worktree / "BACKLOG.md").write_text(updated, encoding="utf-8")
            runner(["git", "add", "BACKLOG.md"], worktree)
            committed = runner(
                [
                    "git",
                    "commit",
                    "-q",
                    "-m",
                    f"docs: record the Codecov patch-coverage gap on PR #{comment.pr}",
                ],
                worktree,
            )
            if committed.returncode != 0:
                return "failed", committed.stderr.strip() or "git commit failed"
            pushed = runner(["git", "push", "-q", "-u", "origin", branch], worktree)
            if pushed.returncode != 0:
                return "failed", pushed.stderr.strip() or "git push failed"
            created = runner(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    f"docs: record the Codecov patch-coverage gap on PR #{comment.pr}",
                    "--body",
                    "Adds a pending backlog item for the patch-coverage gap Codecov reported "
                    f"on #{comment.pr}: {comment.comment_url}",
                ],
                worktree,
            )
            if created.returncode != 0:
                return "failed", created.stderr.strip() or "gh pr create failed"
            return "opened", created.stdout.strip().splitlines()[-1] if created.stdout.strip() else branch
        finally:
            runner(["git", "worktree", "remove", "--force", str(worktree)], repo_dir)


def write_candidates(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(CANDIDATE_HEADER)]
    for row in rows:
        lines.append("\t".join(str(row.get(name, "")).replace("\t", " ").replace("\n", " ") for name in CANDIDATE_HEADER))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def schedule_repair(config: MonitorConfig, count: int, runner: Runner = run_command) -> str:
    if not config.trigger_repair or count == 0:
        return "not requested"
    if config.dry_run or config.messages_file is not None:
        return "skipped (dry run or offline messages file)"
    result = runner(
        [
            "systemd-run",
            "--user",
            f"--unit={config.trigger_unit}",
            f"--on-active={config.trigger_delay_minutes * 60}s",
            "--collect",
            "--description=Delayed Codecov coverage repair trigger",
            "systemctl",
            "--user",
            "start",
            config.trigger_service,
        ],
        None,
    )
    output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode == 0:
        return f"scheduled in {config.trigger_delay_minutes} minutes"
    if "already" in output.lower():
        return "already scheduled"
    return f"failed: {output or result.returncode}"


# --------------------------------------------------------------------------- orchestration


def load_messages(config: MonitorConfig) -> list[dict[str, Any]]:
    if config.messages_file is not None:
        payload = json.loads(config.messages_file.read_text(encoding="utf-8"))
        messages = payload.get("messages", []) if isinstance(payload, dict) else payload
        return [m for m in messages if isinstance(m, dict)]
    common = ci._load_shock_relay_common(config.shock_relay_root)
    try:
        gmail_config = common.load_config(str(config.gmail_config))
        payload = common.list_messages(
            gmail_config,
            mailboxes=[config.mailbox],
            limit=config.limit,
            unseen_only=False,
            from_contains=config.from_filter,
            subject_contains="",
            since_days=config.since_days,
        )
    except Exception as exc:
        raise MonitorError(f"gmail inbox check failed: {exc}") from exc
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise MonitorError("shock-relay returned an invalid message payload")
    return [m for m in messages if isinstance(m, dict)]


def process(
    config: MonitorConfig,
    comments: list[CodecovComment],
    state: dict[str, Any],
    *,
    today: str,
    runner: Runner = run_command,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the policy to parsed comments. Returns (new_state, summary)."""
    prs = state.get("prs", {}) if isinstance(state.get("prs"), dict) else {}
    prs = dict(prs)
    now = datetime.now(timezone.utc).isoformat()

    latest: dict[str, CodecovComment] = {}
    for comment in comments:
        previous = latest.get(comment_key(comment))
        if previous is None or (comment.comment_id or "") >= (previous.comment_id or ""):
            latest[comment_key(comment)] = comment

    summary: dict[str, Any] = {"seen": len(comments), "actions": [], "candidates": 0}
    candidate_rows: list[dict[str, str]] = []
    for key, comment in sorted(latest.items()):
        record = dict(prs.get(key) or {})
        if not needs_action(comment, config.min_patch_pct):
            if record and comment.missing_lines == 0:
                record["resolved_at"] = record.get("resolved_at") or now
                prs[key] = record
            continue
        is_new_comment = record.get("comment_id") != comment.comment_id
        if not is_new_comment:
            continue
        action: dict[str, Any] = {
            "pr": key,
            "patch_pct": comment.patch_pct,
            "missing_lines": comment.missing_lines,
        }
        record.update(
            {
                "repo_slug": comment.repo_slug,
                "repo_rel": comment.repo_rel,
                "pr": comment.pr,
                "comment_id": comment.comment_id,
                "comment_url": comment.comment_url,
                "patch_pct": comment.patch_pct,
                "missing_lines": comment.missing_lines,
                "last_seen_at": now,
            }
        )
        record.pop("resolved_at", None)

        if config.add_backlog and not record.get("backlog_pr"):
            if comment.repo_rel is None:
                action["backlog"] = "skipped (repository is not checked out locally)"
            elif config.dry_run:
                action["backlog"] = "dry run"
            else:
                outcome, detail = add_backlog_item(
                    comment, config.portfolio_root / comment.repo_rel, today, runner
                )
                action["backlog"] = f"{outcome}: {detail}"
                if outcome in {"opened", "exists"}:
                    record["backlog_pr"] = detail

        attempts = int(record.get("repair_attempts") or 0)
        pull = pull_request_state(comment.repo_slug, comment.pr, runner) if config.trigger_repair else None
        if config.trigger_repair and comment.repo_rel is not None and pull and pull.get("state") == "OPEN":
            if pull.get("isCrossRepository"):
                action["repair"] = "skipped (pull request is from a fork)"
            elif attempts >= config.max_attempts:
                action["repair"] = f"skipped (already tried {attempts} times)"
            else:
                record["repair_attempts"] = attempts + 1
                candidate_rows.append(
                    {
                        "status": "candidate",
                        "repo_slug": comment.repo_slug,
                        "repo_rel": comment.repo_rel,
                        "pr": str(comment.pr),
                        "branch": str(pull.get("headRefName") or ""),
                        "patch_pct": "" if comment.patch_pct is None else f"{comment.patch_pct:g}",
                        "missing_lines": str(comment.missing_lines),
                        "files": describe_files(comment.files),
                        "comment_url": comment.comment_url,
                        "detected_at": now,
                    }
                )
                action["repair"] = "candidate"
        elif config.trigger_repair:
            action["repair"] = "skipped (pull request is not open or not found)"

        prs[key] = record
        summary["actions"].append(action)

    summary["candidates"] = len(candidate_rows)
    if candidate_rows and not config.dry_run:
        write_candidates(config.candidate_file, candidate_rows)
    summary["trigger"] = schedule_repair(config, len(candidate_rows), runner)
    return {"checked_at": now, "prs": prs}, summary


def parse_args(argv: list[str] | None = None) -> MonitorConfig:
    env = os.environ.get
    portfolio_default = ci.PORTFOLIO_ROOT_DEFAULT
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--portfolio-root", type=Path, default=Path(env("PORTFOLIO_ROOT", str(portfolio_default))))
    parser.add_argument("--shock-relay-root", type=Path, default=ci.SHOCK_RELAY_ROOT_DEFAULT)
    parser.add_argument(
        "--gmail-config",
        type=Path,
        default=Path(env("CODECOV_EMAIL_GMAIL_CONFIG", str(ci.DEFAULT_GMAIL_CONFIG))),
    )
    parser.add_argument(
        "--state-file", type=Path, default=Path(env("CODECOV_EMAIL_STATE_FILE", str(DEFAULT_STATE_FILE)))
    )
    parser.add_argument(
        "--candidate-file",
        type=Path,
        default=Path(env("CODECOV_EMAIL_CANDIDATE_FILE", str(DEFAULT_CANDIDATE_FILE))),
    )
    parser.add_argument("--mailbox", default=env("CODECOV_EMAIL_MAILBOX", ci.DEFAULT_MAILBOX))
    parser.add_argument("--from-filter", default=env("CODECOV_EMAIL_FROM_FILTER", DEFAULT_FROM_FILTER))
    parser.add_argument("--since-days", type=int, default=int(env("CODECOV_EMAIL_SINCE_DAYS", DEFAULT_SINCE_DAYS)))
    parser.add_argument("--limit", type=int, default=int(env("CODECOV_EMAIL_LIMIT", DEFAULT_LIMIT)))
    parser.add_argument(
        "--min-patch-pct",
        type=float,
        default=float(env("CODECOV_EMAIL_MIN_PATCH_PCT")) if env("CODECOV_EMAIL_MIN_PATCH_PCT") else None,
        help="Also act when patch coverage is below this percentage, even if Codecov did not flag it.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=int(env("CODECOV_EMAIL_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)),
        help="Repair attempts per pull request (default: %(default)s).",
    )
    parser.add_argument("--messages-file", type=Path, help="Read messages from a JSON file (offline).")
    parser.add_argument(
        "--no-backlog",
        action="store_true",
        default=not ci.env_bool("CODECOV_EMAIL_ADD_BACKLOG", True),
        help="Do not open backlog pull requests.",
    )
    parser.add_argument(
        "--no-trigger-repair",
        action="store_true",
        default=not ci.env_bool("CODECOV_EMAIL_TRIGGER_REPAIR", True),
        help="Do not schedule the agentic repair.",
    )
    parser.add_argument(
        "--trigger-delay-minutes",
        type=int,
        default=int(env("CODECOV_EMAIL_TRIGGER_REPAIR_DELAY_MINUTES", DEFAULT_TRIGGER_DELAY_MINUTES)),
    )
    parser.add_argument(
        "--trigger-service", default=env("CODECOV_EMAIL_TRIGGER_REPAIR_SERVICE", DEFAULT_TRIGGER_SERVICE)
    )
    parser.add_argument(
        "--trigger-unit", default=env("CODECOV_EMAIL_TRIGGER_REPAIR_UNIT", DEFAULT_TRIGGER_UNIT)
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; change nothing.")
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args(argv)
    return MonitorConfig(
        portfolio_root=args.portfolio_root.resolve(),
        shock_relay_root=args.shock_relay_root,
        gmail_config=args.gmail_config,
        state_file=args.state_file,
        candidate_file=args.candidate_file,
        mailbox=args.mailbox,
        from_filter=args.from_filter,
        since_days=args.since_days,
        limit=args.limit,
        min_patch_pct=args.min_patch_pct,
        max_attempts=args.max_attempts,
        messages_file=args.messages_file,
        add_backlog=not args.no_backlog,
        trigger_repair=not args.no_trigger_repair,
        trigger_delay_minutes=args.trigger_delay_minutes,
        trigger_service=args.trigger_service,
        trigger_unit=args.trigger_unit,
        dry_run=args.dry_run,
        output_json=args.output_json,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    state = ci.load_state(config.state_file)
    repo_index = ci.build_repo_index(config.portfolio_root)
    comments = [
        parsed
        for message in load_messages(config)
        if (parsed := parse_codecov_comment(message, repo_index)) is not None
    ]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_state, summary = process(config, comments, state, today=today)
    if not config.dry_run:
        ci.save_state(config.state_file, new_state)
    if config.output_json:
        print(json.dumps({"summary": summary}, indent=2, sort_keys=True))
    else:
        print(f"info: codecov comments seen={summary['seen']} new={len(summary['actions'])} candidates={summary['candidates']}")
        for action in summary["actions"]:
            print(
                f"info: {action['pr']} patch={action.get('patch_pct')} missing={action['missing_lines']} "
                f"backlog={action.get('backlog', 'n/a')} repair={action.get('repair', 'n/a')}"
            )
        print(f"info: repair trigger: {summary['trigger']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
