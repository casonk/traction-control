#!/usr/bin/env python3
"""Monitor Gmail inbox messages for GitHub Actions failure notifications."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_ROOT_DEFAULT = REPO_ROOT.parents[1]
SHOCK_RELAY_ROOT_DEFAULT = (REPO_ROOT.parent / "shock-relay").resolve()
DEFAULT_GMAIL_CONFIG = SHOCK_RELAY_ROOT_DEFAULT / "services" / "gmail-imap" / "config.local.yaml"
DEFAULT_STATE_FILE = Path.home() / ".local" / "share" / "traction-control" / "github-ci-email-monitor.json"
DEFAULT_MAILBOX = "INBOX"
DEFAULT_FROM_FILTER = "notifications@github.com"
DEFAULT_SUBJECT_FILTER = "Run failed:"
DEFAULT_PROCESSED_LABEL = "GitHub/CI Failure Processed"
DEFAULT_LIMIT = 20
DEFAULT_SINCE_DAYS = 14
MAX_REPO_DEPTH = 4
BAD_RUN_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "stale",
    "startup_failure",
    "timed_out",
}

SUBJECT_RE = re.compile(
    r"^\[(?P<repo_slug>[^\]]+)\]\s+Run failed:\s+(?P<workflow_and_branch>.+?)\s+\((?P<head_sha>[0-9a-fA-F]{7,40})\)\s*$"
)
RUN_URL_RE = re.compile(r"https://github\.com/(?P<repo_slug>[^/\s]+/[^/\s]+)/actions/runs/(?P<run_id>\d+)")
JOB_BLOCK_RE = re.compile(
    r"\n(?P<job>[^\n]+)\n\n(?P<status>Failed(?: in [^\n]+)?|Cancelled|Success(?: in [^\n]+)?)\n",
    re.MULTILINE,
)
JOB_BULLET_RE = re.compile(
    r"^\s*(?:\*|-)\s+(?P<job>.+?)\s+(?P<status>failed|cancelled|success|succeeded)\s+\((?P<count>\d+)\s+annotations?\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
ANNOTATION_RE = re.compile(
    r"\[annotations for (?P<job>.+?)\n(?P<count>\d+)\]\(",
    re.DOTALL,
)


class MonitorError(RuntimeError):
    """Raised when the CI email monitor cannot complete safely."""


@dataclass(frozen=True)
class MonitorConfig:
    portfolio_root: Path
    shock_relay_root: Path
    gmail_config: Path
    state_file: Path
    mailbox: str
    from_filter: str
    subject_filter: str
    processed_label: str
    limit: int
    since_days: int
    unseen_only: bool
    messages_file: Path | None
    fixed_notify_to: tuple[str, ...]
    fixed_notify_subject_prefix: str
    output_json: bool


@dataclass(frozen=True)
class GitHubCiEmail:
    message_id: str
    subject: str
    sender: str
    received_at: str
    repo_slug: str
    repo_rel: str | None
    workflow_name: str
    branch: str
    head_sha: str
    run_id: str | None
    run_url: str | None
    failed_jobs: tuple[str, ...]
    cancelled_jobs: tuple[str, ...]
    annotation_counts: dict[str, int]
    snippet: str


@dataclass(frozen=True)
class ParsedDetection:
    email: GitHubCiEmail
    mailbox: str
    uid: int | None


@dataclass(frozen=True)
class FixedCiRun:
    key: str
    repo_slug: str
    repo_rel: str | None
    branch: str
    failed_head_sha: str
    fixed_head_sha: str
    workflow_names: tuple[str, ...]
    run_urls: tuple[str, ...]


def env_bool(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def parse_args() -> MonitorConfig:
    parser = argparse.ArgumentParser(
        description="Scan Gmail inbox for GitHub Actions failure emails and record new detections."
    )
    parser.add_argument(
        "--portfolio-root",
        default=os.environ.get("PORTFOLIO_ROOT", ""),
        help="Portfolio root to scan for local GitHub repos. Defaults to ../.. from traction-control.",
    )
    parser.add_argument(
        "--shock-relay-root",
        default=os.environ.get("GITHUB_CI_EMAIL_SHOCK_RELAY_ROOT", str(SHOCK_RELAY_ROOT_DEFAULT)),
        help="Path to the sibling shock-relay repo.",
    )
    parser.add_argument(
        "--gmail-config",
        default=os.environ.get("GITHUB_CI_EMAIL_GMAIL_CONFIG", str(DEFAULT_GMAIL_CONFIG)),
        help="Path to shock-relay's gmail-imap config.local.yaml.",
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get("GITHUB_CI_EMAIL_STATE_FILE", str(DEFAULT_STATE_FILE)),
        help="Path to the local JSON state file.",
    )
    parser.add_argument(
        "--mailbox",
        default=os.environ.get("GITHUB_CI_EMAIL_MAILBOX", DEFAULT_MAILBOX),
        help=f"Mailbox to scan (default: {DEFAULT_MAILBOX}).",
    )
    parser.add_argument(
        "--from-filter",
        default=os.environ.get("GITHUB_CI_EMAIL_FROM_FILTER", DEFAULT_FROM_FILTER),
        help=f"Sender substring filter (default: {DEFAULT_FROM_FILTER!r}).",
    )
    parser.add_argument(
        "--subject-filter",
        default=os.environ.get("GITHUB_CI_EMAIL_SUBJECT_FILTER", DEFAULT_SUBJECT_FILTER),
        help=f"Subject substring filter (default: {DEFAULT_SUBJECT_FILTER!r}).",
    )
    parser.add_argument(
        "--processed-label",
        default=os.environ.get(
            "GITHUB_CI_EMAIL_PROCESSED_LABEL", DEFAULT_PROCESSED_LABEL
        ),
        help=(
            "Gmail label to apply after a matching message is processed "
            f"(default: {DEFAULT_PROCESSED_LABEL!r})."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("GITHUB_CI_EMAIL_LIMIT", str(DEFAULT_LIMIT))),
        help=f"Maximum matching inbox messages to inspect (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=int(os.environ.get("GITHUB_CI_EMAIL_SINCE_DAYS", str(DEFAULT_SINCE_DAYS))),
        help=f"Only inspect messages from the last N days (default: {DEFAULT_SINCE_DAYS}).",
    )
    parser.add_argument(
        "--unseen",
        action="store_true",
        help="Restrict the scan to unseen inbox messages.",
    )
    parser.add_argument(
        "--messages-file",
        default=os.environ.get("GITHUB_CI_EMAIL_MESSAGES_FILE", ""),
        help="Optional JSON file containing pre-fetched Gmail messages for offline parsing/tests.",
    )
    parser.add_argument(
        "--fixed-notify-to",
        action="append",
        default=[],
        help=(
            "Recipient for fixed CI notifications. May be supplied multiple times or "
            "comma-separated. Defaults to GITHUB_CI_FIXED_NOTIFY_TO."
        ),
    )
    parser.add_argument(
        "--fixed-notify-subject-prefix",
        default=os.environ.get(
            "GITHUB_CI_FIXED_NOTIFY_SUBJECT_PREFIX",
            "[traction-control] GitHub CI fixed",
        ),
        help="Subject prefix for fixed CI notification emails.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the final summary as JSON instead of human-readable log lines.",
    )
    args = parser.parse_args()

    portfolio_root_raw = (
        args.portfolio_root
        or str(Path(REPO_ROOT / "../..").resolve())
        or str(PORTFOLIO_ROOT_DEFAULT)
    )
    return MonitorConfig(
        portfolio_root=Path(portfolio_root_raw).expanduser().resolve(),
        shock_relay_root=Path(args.shock_relay_root).expanduser().resolve(),
        gmail_config=Path(args.gmail_config).expanduser().resolve(),
        state_file=Path(args.state_file).expanduser(),
        mailbox=str(args.mailbox).strip() or DEFAULT_MAILBOX,
        from_filter=str(args.from_filter).strip() or DEFAULT_FROM_FILTER,
        subject_filter=str(args.subject_filter).strip() or DEFAULT_SUBJECT_FILTER,
        processed_label=str(args.processed_label).strip() or DEFAULT_PROCESSED_LABEL,
        limit=max(1, int(args.limit)),
        since_days=max(1, int(args.since_days)),
        unseen_only=bool(args.unseen or env_bool("GITHUB_CI_EMAIL_UNSEEN_ONLY")),
        messages_file=Path(args.messages_file).expanduser().resolve() if args.messages_file else None,
        fixed_notify_to=tuple(
            _split_csv_values(
                [
                    os.environ.get("GITHUB_CI_FIXED_NOTIFY_TO", ""),
                    *args.fixed_notify_to,
                ]
            )
        ),
        fixed_notify_subject_prefix=str(args.fixed_notify_subject_prefix).strip()
        or "[traction-control] GitHub CI fixed",
        output_json=bool(args.json),
    )


def _split_csv_values(values: Iterable[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for part in str(value or "").split(","):
            cleaned = part.strip()
            if cleaned:
                parts.append(cleaned)
    return list(dict.fromkeys(parts))


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise MonitorError(f"invalid JSON in state file {path}: {exc}") from exc


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_shock_relay_common(shock_relay_root: Path):
    module_dir = shock_relay_root / "services" / "gmail-imap"
    if not module_dir.is_dir():
        raise MonitorError(f"shock-relay gmail-imap dir not found: {module_dir}")
    module_dir_text = str(module_dir)
    if module_dir_text not in sys.path:
        sys.path.insert(0, module_dir_text)
    try:
        return importlib.import_module("common")
    except ImportError as exc:
        raise MonitorError(f"cannot import shock-relay gmail-imap common.py: {exc}") from exc


def load_messages(config: MonitorConfig) -> list[dict[str, Any]]:
    if config.messages_file is not None:
        try:
            payload = json.loads(config.messages_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise MonitorError(f"messages file not found: {config.messages_file}") from exc
        except json.JSONDecodeError as exc:
            raise MonitorError(f"invalid JSON in messages file {config.messages_file}: {exc}") from exc
        if isinstance(payload, dict):
            messages = payload.get("messages", [])
        else:
            messages = payload
        if not isinstance(messages, list):
            raise MonitorError("messages file must contain a list or a dict with a 'messages' list")
        return [item for item in messages if isinstance(item, dict)]

    common = _load_shock_relay_common(config.shock_relay_root)
    try:
        gmail_config = common.load_config(str(config.gmail_config))
        payload = common.list_messages(
            gmail_config,
            mailboxes=[config.mailbox],
            limit=config.limit,
            unseen_only=config.unseen_only,
            from_contains=config.from_filter,
            subject_contains=config.subject_filter,
            since_days=config.since_days,
        )
    except Exception as exc:
        raise MonitorError(f"gmail inbox check failed: {exc}") from exc
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise MonitorError("shock-relay returned an invalid message payload")
    return [item for item in messages if isinstance(item, dict)]


def parse_github_slug(remote_url: str) -> str | None:
    cleaned = remote_url.strip()
    if cleaned.startswith("git@github.com:"):
        cleaned = cleaned.removeprefix("git@github.com:")
    elif cleaned.startswith("https://github.com/"):
        cleaned = cleaned.removeprefix("https://github.com/")
    elif cleaned.startswith("ssh://git@github.com/"):
        cleaned = cleaned.removeprefix("ssh://git@github.com/")
    else:
        return None
    cleaned = cleaned.removesuffix(".git").strip("/")
    return cleaned or None


def _git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def build_repo_index(portfolio_root: Path) -> dict[str, str]:
    repo_index: dict[str, str] = {}
    for git_dir in portfolio_root.rglob(".git"):
        try:
            rel_git = git_dir.relative_to(portfolio_root)
        except ValueError:
            continue
        if len(rel_git.parts) > MAX_REPO_DEPTH:
            continue
        repo_dir = git_dir.parent
        origin = _git(["remote", "get-url", "origin"], cwd=repo_dir)
        slug = parse_github_slug(origin)
        if not slug:
            continue
        repo_index.setdefault(slug, str(repo_dir.relative_to(portfolio_root)))
    return repo_index


def parse_workflow_and_branch(value: str) -> tuple[str, str]:
    trimmed = value.strip()
    if " - " not in trimmed:
        return trimmed, ""
    workflow_name, branch = trimmed.rsplit(" - ", 1)
    return workflow_name.strip(), branch.strip()


def parse_annotation_counts(body: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in JOB_BULLET_RE.finditer(body):
        job = " ".join(match.group("job").split())
        counts[job] = int(match.group("count"))
    for match in ANNOTATION_RE.finditer(body):
        job = " ".join(match.group("job").split())
        counts[job] = int(match.group("count"))
    return counts


def parse_job_statuses(body: str) -> tuple[list[str], list[str]]:
    failed_jobs: list[str] = []
    cancelled_jobs: list[str] = []
    for match in JOB_BULLET_RE.finditer(body):
        job = " ".join(match.group("job").split())
        status = match.group("status").strip().lower()
        if status == "failed":
            failed_jobs.append(job)
        elif status == "cancelled":
            cancelled_jobs.append(job)
    for match in JOB_BLOCK_RE.finditer(body):
        job = " ".join(match.group("job").split())
        status = match.group("status").strip().lower()
        if status.startswith("failed"):
            failed_jobs.append(job)
        elif status.startswith("cancelled"):
            cancelled_jobs.append(job)
    return list(dict.fromkeys(failed_jobs)), list(dict.fromkeys(cancelled_jobs))


def parse_uid(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def mailbox_name_candidates(name: str) -> list[str]:
    cleaned = str(name or "").strip()
    if not cleaned:
        return []
    candidates: list[str] = []
    if " " in cleaned and not cleaned.startswith('"'):
        candidates.append(f'"{cleaned}"')
    candidates.append(cleaned)
    return list(dict.fromkeys(candidates))


def apply_gmail_label(conn: Any, uid: int, label: str) -> None:
    last_status = ""
    last_data: Any = []
    last_exception: Exception | None = None
    for candidate in mailbox_name_candidates(label):
        try:
            conn.create(candidate)
        except Exception as exc:
            last_exception = exc
            continue
        try:
            status, data = conn.uid("copy", str(uid), candidate)
        except Exception as exc:
            last_exception = exc
            continue
        if status == "OK":
            return
        last_status, last_data = status, data
    if last_exception is not None:
        raise last_exception
    raise MonitorError(
        f"IMAP UID copy failed while applying label {label!r}: status={last_status} data={last_data!r}"
    )


def parse_ci_email(message: dict[str, Any], repo_index: dict[str, str]) -> GitHubCiEmail | None:
    subject = str(message.get("subject") or "").strip()
    sender = str(message.get("from") or "").strip()
    body = str(message.get("text") or message.get("html") or "").strip()
    snippet = str(message.get("snippet") or "").strip()
    parse_text = "\n".join(part for part in (body, snippet) if part)
    if not subject or not sender:
        return None
    subject_match = SUBJECT_RE.match(subject)
    if not subject_match:
        return None

    repo_slug = subject_match.group("repo_slug").strip()
    workflow_name, branch = parse_workflow_and_branch(subject_match.group("workflow_and_branch"))
    head_sha = subject_match.group("head_sha")

    run_match = RUN_URL_RE.search(parse_text)
    run_url = run_match.group(0) if run_match else None
    run_id = run_match.group("run_id") if run_match else None

    failed_jobs, cancelled_jobs = parse_job_statuses(parse_text)
    annotation_counts = parse_annotation_counts(parse_text)

    message_id = str(message.get("message_id") or "").strip()
    if not message_id and run_id:
        message_id = f"<github-actions-run-{run_id}>"

    return GitHubCiEmail(
        message_id=message_id,
        subject=subject,
        sender=sender,
        received_at=str(message.get("date") or "").strip(),
        repo_slug=repo_slug,
        repo_rel=repo_index.get(repo_slug),
        workflow_name=workflow_name,
        branch=branch,
        head_sha=head_sha,
        run_id=run_id,
        run_url=run_url,
        failed_jobs=tuple(failed_jobs),
        cancelled_jobs=tuple(cancelled_jobs),
        annotation_counts=annotation_counts,
        snippet=snippet,
    )


def detection_key(email: GitHubCiEmail) -> str:
    return email.run_id or email.message_id or email.subject


def summarize_for_humans(
    *,
    parsed: list[GitHubCiEmail],
    new_items: list[GitHubCiEmail],
    tagged_count: int,
) -> list[str]:
    lines: list[str] = []
    if not parsed:
        lines.append("info: no matching GitHub CI failure emails found in the configured inbox scan")
        return lines

    lines.append(
        f"info: matching GitHub CI failure emails found={len(parsed)} new={len(new_items)} tagged={tagged_count}"
    )
    for item in new_items:
        failed_jobs = ", ".join(item.failed_jobs) or "-"
        repo_rel = item.repo_rel or "-"
        run_ref = item.run_id or "-"
        lines.append(
            "WARNING GitHub CI failure email detected: "
            f"repo={item.repo_slug} repo_rel={repo_rel} workflow={item.workflow_name or '-'} "
            f"branch={item.branch or '-'} sha={item.head_sha or '-'} run_id={run_ref} "
            f"failed_jobs={failed_jobs} url={item.run_url or '-'}"
        )
    if parsed and not new_items:
        lines.append("info: no new GitHub CI failure email detections since the last state snapshot")
    return lines


def emit_json_summary(
    parsed: list[GitHubCiEmail],
    new_items: list[GitHubCiEmail],
    tagged_keys: set[str],
    fixed_notifications: list[FixedCiRun],
) -> str:
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "matching_messages": len(parsed),
        "new_detections": len(new_items),
        "tagged_messages": len(tagged_keys),
        "fixed_notifications": len(fixed_notifications),
        "emails": [asdict(item) for item in parsed],
        "new_emails": [asdict(item) for item in new_items],
        "tagged_detection_keys": sorted(tagged_keys),
        "fixed": [asdict(item) for item in fixed_notifications],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _gh_json(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh command failed"
        raise MonitorError(message)
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise MonitorError(f"gh returned invalid JSON: {exc}") from exc


def latest_branch_run_state(repo_slug: str, branch: str) -> dict[str, Any] | None:
    runs = _gh_json(
        [
            "run",
            "list",
            "--repo",
            repo_slug,
            "--branch",
            branch,
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,status,conclusion,url,workflowName,createdAt,event",
        ]
    )
    if not isinstance(runs, list) or not runs:
        return None
    latest_head_sha = str(runs[0].get("headSha") or "")
    if not latest_head_sha:
        return None
    latest_runs = [run for run in runs if str(run.get("headSha") or "") == latest_head_sha]
    if not latest_runs:
        return None
    pending = [run for run in latest_runs if run.get("status") != "completed"]
    bad = [
        run
        for run in latest_runs
        if str(run.get("conclusion") or "") in BAD_RUN_CONCLUSIONS
    ]
    successes = [
        run for run in latest_runs if str(run.get("conclusion") or "") == "success"
    ]
    return {
        "head_sha": latest_head_sha,
        "is_green": not pending and not bad and bool(successes),
        "workflow_names": tuple(
            dict.fromkeys(str(run.get("workflowName") or "") for run in latest_runs if run.get("workflowName"))
        ),
        "run_urls": tuple(
            str(run.get("url") or "") for run in latest_runs if run.get("url")
        ),
    }


def find_fixed_runs(config: MonitorConfig, state: dict[str, Any]) -> list[FixedCiRun]:
    if not config.fixed_notify_to:
        return []
    known_runs = state.get("runs", {})
    if not isinstance(known_runs, dict):
        return []

    fixed: list[FixedCiRun] = []
    checked: dict[tuple[str, str], dict[str, Any] | None] = {}
    for key, record in known_runs.items():
        if not isinstance(record, dict) or record.get("fixed_notified_at"):
            continue
        repo_slug = str(record.get("repo_slug") or "").strip()
        branch = str(record.get("branch") or "").strip()
        failed_head_sha = str(record.get("head_sha") or "").strip()
        if not repo_slug or not branch or not failed_head_sha:
            continue
        cache_key = (repo_slug, branch)
        if cache_key not in checked:
            checked[cache_key] = latest_branch_run_state(repo_slug, branch)
        branch_state = checked[cache_key]
        if not branch_state or not branch_state["is_green"]:
            continue
        fixed_head_sha = str(branch_state["head_sha"] or "")
        if not fixed_head_sha or fixed_head_sha == failed_head_sha:
            continue
        fixed.append(
            FixedCiRun(
                key=str(key),
                repo_slug=repo_slug,
                repo_rel=record.get("repo_rel"),
                branch=branch,
                failed_head_sha=failed_head_sha,
                fixed_head_sha=fixed_head_sha,
                workflow_names=tuple(branch_state["workflow_names"]),
                run_urls=tuple(branch_state["run_urls"]),
            )
        )
    return fixed


def send_fixed_notifications(config: MonitorConfig, fixed_runs: list[FixedCiRun]) -> None:
    if not fixed_runs:
        return
    common = _load_shock_relay_common(config.shock_relay_root)
    try:
        gmail_config = common.load_config(str(config.gmail_config))
    except Exception as exc:
        raise MonitorError(f"cannot load Gmail config for fixed notification email: {exc}") from exc

    for item in fixed_runs:
        workflows = ", ".join(item.workflow_names) or "workflows"
        run_urls = "\n".join(f"- {url}" for url in item.run_urls) or "- no run URL recorded"
        subject = f"{config.fixed_notify_subject_prefix}: {item.repo_slug}"
        body = (
            f"GitHub Actions is green again for {item.repo_slug}.\n\n"
            f"Repository: {item.repo_slug}\n"
            f"Local path: {item.repo_rel or '-'}\n"
            f"Branch: {item.branch}\n"
            f"Failed SHA: {item.failed_head_sha}\n"
            f"Fixed SHA: {item.fixed_head_sha}\n"
            f"Workflow(s): {workflows}\n\n"
            f"Current run(s):\n{run_urls}\n"
        )
        try:
            common.send_email(
                gmail_config,
                to_addresses=list(config.fixed_notify_to),
                subject=subject,
                body=body,
                headers={"X-Portfolio-Service": "traction-control"},
            )
        except Exception as exc:
            raise MonitorError(f"failed sending fixed CI notification email: {exc}") from exc


def should_apply_processed_label(
    detection: ParsedDetection,
    known_runs: dict[str, Any],
    *,
    processed_label: str,
) -> bool:
    if not processed_label:
        return False
    prior = known_runs.get(detection_key(detection.email))
    if not isinstance(prior, dict):
        return True
    return not (
        prior.get("processed_label") == processed_label
        and prior.get("processed_label_applied_at")
    )


def apply_processed_labels(
    config: MonitorConfig,
    detections: Iterable[ParsedDetection],
    known_runs: dict[str, Any],
) -> set[str]:
    if config.messages_file is not None or not config.processed_label:
        return set()

    pending = [
        detection
        for detection in detections
        if should_apply_processed_label(
            detection,
            known_runs,
            processed_label=config.processed_label,
        )
    ]
    if not pending:
        return set()

    for detection in pending:
        if detection.uid is None or not detection.mailbox:
            raise MonitorError(
                "cannot apply processed Gmail label because the monitor message is missing "
                f"mailbox/uid metadata (subject={detection.email.subject!r})"
            )

    common = _load_shock_relay_common(config.shock_relay_root)
    try:
        gmail_config = common.load_config(str(config.gmail_config))
    except Exception as exc:
        raise MonitorError(f"cannot load Gmail config for label application: {exc}") from exc

    labeled_keys: set[str] = set()
    current_mailbox = ""
    try:
        with common.open_imap_connection(gmail_config) as conn:
            for detection in pending:
                assert detection.uid is not None
                if detection.mailbox != current_mailbox:
                    try:
                        status, _ = common.select_mailbox(
                            conn, detection.mailbox, readonly=False
                        )
                    except Exception as exc:
                        raise MonitorError(
                            "failed selecting the Gmail mailbox for processed-label application "
                            f"(mailbox={detection.mailbox!r}): {exc}"
                        ) from exc
                    if status != "OK":
                        raise MonitorError(
                            "failed selecting the Gmail mailbox for processed-label application "
                            f"(mailbox={detection.mailbox!r}, status={status})"
                        )
                    current_mailbox = detection.mailbox
                try:
                    apply_gmail_label(conn, detection.uid, config.processed_label)
                except Exception as exc:
                    raise MonitorError(
                        "failed applying the processed Gmail label "
                        f"{config.processed_label!r} to uid={detection.uid} "
                        f"in mailbox={detection.mailbox!r}: {exc}"
                    ) from exc
                labeled_keys.add(detection_key(detection.email))
    except MonitorError:
        raise
    except Exception as exc:
        raise MonitorError(
            f"processed Gmail label application failed unexpectedly: {exc}"
        ) from exc
    return labeled_keys


def update_state(
    existing: dict[str, Any],
    parsed: Iterable[GitHubCiEmail],
    *,
    processed_label: str,
    labeled_keys: set[str],
    fixed_notifications: Iterable[FixedCiRun],
) -> dict[str, Any]:
    runs = existing.get("runs", {})
    if not isinstance(runs, dict):
        runs = {}

    updated_runs = dict(runs)
    checked_at = datetime.now(timezone.utc).isoformat()
    for item in parsed:
        key = detection_key(item)
        previous = updated_runs.get(key)
        record = {
            "repo_slug": item.repo_slug,
            "repo_rel": item.repo_rel,
            "workflow_name": item.workflow_name,
            "branch": item.branch,
            "head_sha": item.head_sha,
            "run_id": item.run_id,
            "run_url": item.run_url,
            "message_id": item.message_id,
            "subject": item.subject,
            "received_at": item.received_at,
            "failed_jobs": list(item.failed_jobs),
            "annotation_counts": item.annotation_counts,
            "last_seen_at": checked_at,
        }
        if isinstance(previous, dict):
            prior_label = str(previous.get("processed_label") or "").strip()
            prior_applied_at = str(previous.get("processed_label_applied_at") or "").strip()
            if prior_label:
                record["processed_label"] = prior_label
            if prior_applied_at:
                record["processed_label_applied_at"] = prior_applied_at
            for fixed_key in (
                "fixed_notified_at",
                "fixed_head_sha",
                "fixed_workflows",
                "fixed_run_urls",
            ):
                if fixed_key in previous:
                    record[fixed_key] = previous[fixed_key]
        if key in labeled_keys:
            record["processed_label"] = processed_label
            record["processed_label_applied_at"] = checked_at
        updated_runs[key] = record
    for item in fixed_notifications:
        record = updated_runs.get(item.key)
        if not isinstance(record, dict):
            continue
        updated = dict(record)
        updated["fixed_notified_at"] = checked_at
        updated["fixed_head_sha"] = item.fixed_head_sha
        updated["fixed_workflows"] = list(item.workflow_names)
        updated["fixed_run_urls"] = list(item.run_urls)
        updated_runs[item.key] = updated
    return {
        "checked_at": checked_at,
        "runs": updated_runs,
    }


def main() -> int:
    config = parse_args()
    state = load_state(config.state_file)
    repo_index = build_repo_index(config.portfolio_root)
    messages = load_messages(config)
    parsed_by_key: dict[str, ParsedDetection] = {}
    for message in messages:
        item = parse_ci_email(message, repo_index)
        if item is None:
            continue
        parsed_by_key.setdefault(
            detection_key(item),
            ParsedDetection(
                email=item,
                mailbox=str(message.get("mailbox") or "").strip(),
                uid=parse_uid(message.get("uid")),
            ),
        )
    parsed_detections = list(parsed_by_key.values())
    parsed = [detection.email for detection in parsed_detections]
    known_runs = state.get("runs", {})
    if not isinstance(known_runs, dict):
        known_runs = {}
    new_items = [item for item in parsed if detection_key(item) not in known_runs]
    tagged_keys = apply_processed_labels(config, parsed_detections, known_runs)
    fixed_notifications = find_fixed_runs(config, state)
    send_fixed_notifications(config, fixed_notifications)

    if config.output_json:
        print(emit_json_summary(parsed, new_items, tagged_keys, fixed_notifications))
    else:
        for line in summarize_for_humans(
            parsed=parsed, new_items=new_items, tagged_count=len(tagged_keys)
        ):
            print(line)
        for item in fixed_notifications:
            print(
                "info: fixed GitHub CI notification sent: "
                f"repo={item.repo_slug} branch={item.branch} "
                f"failed_sha={item.failed_head_sha} fixed_sha={item.fixed_head_sha}"
            )

    save_state(
        config.state_file,
        update_state(
            state,
            parsed,
            processed_label=config.processed_label,
            labeled_keys=tagged_keys,
            fixed_notifications=fixed_notifications,
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
