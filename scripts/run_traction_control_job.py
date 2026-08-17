"""Run a Traction Control launchd job through a fail-closed schedule adapter."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import random
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

STATE_SCHEMA = 1
MAX_STATE_BYTES = 64 * 1024
JOB_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class RunnerError(RuntimeError):
    """A fail-closed runtime adapter error."""


def _non_negative(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _positive(value: str) -> int:
    parsed = _non_negative(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _valid_host(value: str) -> str:
    if len(value) > 253 or value.endswith("."):
        raise argparse.ArgumentTypeError("must be a hostname without a trailing dot")
    if not value or any(
        not HOST_LABEL_RE.fullmatch(label) for label in value.split(".")
    ):
        raise argparse.ArgumentTypeError("must be a valid hostname")
    return value


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    if "--env-file" in argv:
        raise RunnerError(
            "--env-file was removed; render environment_files through Clockwork's "
            "owner-only loader"
        )
    if "--delay-seconds" in argv:
        raise RunnerError(
            "--delay-seconds was removed; re-render the LaunchAgent with the stateful "
            "--startup-delay-seconds adapter"
        )
    try:
        separator = argv.index("--")
    except ValueError as exc:
        raise RunnerError("a command is required after --") from exc
    option_argv = list(argv[:separator])
    command = list(argv[separator + 1 :])
    if not command:
        raise RunnerError("a command is required after --")

    parser = argparse.ArgumentParser(
        description="Run one Traction Control workload under launchd",
        allow_abbrev=False,
    )
    parser.add_argument("--job", required=True)
    parser.add_argument(
        "--schedule-kind", choices=("interval", "calendar", "none"), required=True
    )
    parser.add_argument("--interval-seconds", type=_positive)
    parser.add_argument("--retry-seconds", type=_positive, default=300)
    parser.add_argument("--startup-delay-seconds", type=_non_negative, default=0)
    parser.add_argument("--jitter-seconds", type=_non_negative, default=0)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--network-host", type=_valid_host)
    parser.add_argument("--network-wait-seconds", type=_positive, default=300)
    args = parser.parse_args(option_argv)

    if not JOB_RE.fullmatch(args.job):
        raise RunnerError("--job must use lowercase letters, digits, and hyphens")
    if args.schedule_kind == "interval":
        if args.interval_seconds is None:
            raise RunnerError("interval jobs require --interval-seconds")
        if args.state_dir is None or not args.state_dir.is_absolute():
            raise RunnerError("interval jobs require an absolute --state-dir")
    else:
        if args.interval_seconds is not None or args.state_dir is not None:
            raise RunnerError(
                "--interval-seconds and --state-dir are valid only for interval jobs"
            )
        if args.startup_delay_seconds:
            raise RunnerError(
                "startup delay is supported only by the stateful interval adapter"
            )
    if args.schedule_kind == "none" and args.jitter_seconds:
        raise RunnerError("on-demand jobs cannot request scheduler jitter")
    return args, command


def _validate_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise RunnerError(f"state path is not a directory: {path}")
    if metadata.st_uid != os.getuid():
        raise RunnerError(f"state directory is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RunnerError(f"state directory is not owner-only (mode 0700): {path}")


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    _validate_private_directory(path)


def _open_lock(path: Path) -> int | None:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RunnerError(f"refusing scheduler lock {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RunnerError(f"scheduler lock is not a regular file: {path}")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RunnerError(f"scheduler lock is not current-user owner-only: {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                os.close(fd)
                return None
            raise
    except BaseException:
        if fd >= 0:
            os.close(fd)
        raise
    return fd


def _read_state(path: Path, *, job: str) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RunnerError(f"refusing scheduler state {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RunnerError(f"scheduler state is not a regular file: {path}")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RunnerError(f"scheduler state is not current-user owner-only: {path}")
        if metadata.st_size > MAX_STATE_BYTES:
            raise RunnerError(f"scheduler state is too large: {path}")
        with os.fdopen(fd, encoding="utf-8") as stream:
            fd = -1
            try:
                payload = json.load(stream)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise RunnerError(f"scheduler state is invalid JSON: {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(payload, dict):
        raise RunnerError(f"scheduler state must be a JSON object: {path}")
    if payload.get("schema") != STATE_SCHEMA or payload.get("job") != job:
        raise RunnerError(f"scheduler state identity mismatch: {path}")
    for key in ("startup_not_before", "next_due_epoch"):
        if not isinstance(payload.get(key), int) or payload[key] < 0:
            raise RunnerError(f"scheduler state has invalid {key}: {path}")
    if not isinstance(payload.get("boot_id"), str) or not payload["boot_id"]:
        raise RunnerError(f"scheduler state has invalid boot_id: {path}")
    return payload


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def boot_metadata() -> tuple[str, int]:
    linux_boot_id = Path("/proc/sys/kernel/random/boot_id")
    if linux_boot_id.is_file():
        value = linux_boot_id.read_text(encoding="ascii").strip()
        boot_epoch = None
        for line in Path("/proc/stat").read_text(encoding="ascii").splitlines():
            if line.startswith("btime "):
                try:
                    boot_epoch = int(line.split()[1], 10)
                except (IndexError, ValueError):
                    boot_epoch = None
                break
        if value and boot_epoch is not None:
            return f"linux:{value}", boot_epoch
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError("cannot determine the current boot identity") from exc
    match = re.search(r"\bsec\s*=\s*(\d+)\b", result.stdout)
    if result.returncode != 0 or match is None:
        raise RunnerError("cannot determine the current boot identity")
    boot_epoch = int(match.group(1), 10)
    return f"darwin:{boot_epoch}", boot_epoch


def _prepare_due_interval(
    args: argparse.Namespace,
    *,
    now_epoch: int,
    current_boot_id: str,
    boot_epoch: int,
) -> tuple[bool, int | None, Path | None]:
    assert args.state_dir is not None
    assert args.interval_seconds is not None
    _ensure_private_directory(args.state_dir)
    job_dir = args.state_dir / args.job
    _ensure_private_directory(job_dir)
    lock_fd = _open_lock(job_dir / "schedule.lock")
    if lock_fd is None:
        return False, None, None

    state_path = job_dir / "schedule.json"
    try:
        state = _read_state(state_path, job=args.job)
        if state is None or state["boot_id"] != current_boot_id:
            first_due = max(now_epoch, boot_epoch + args.startup_delay_seconds)
            state = {
                "boot_id": current_boot_id,
                "job": args.job,
                "next_due_epoch": first_due,
                "schema": STATE_SCHEMA,
                "startup_not_before": first_due,
            }
            _write_state(state_path, state)
        if now_epoch < state["next_due_epoch"]:
            os.close(lock_fd)
            return False, None, None
        retry_after = state.get("retry_after_epoch")
        if retry_after is not None:
            if not isinstance(retry_after, int) or retry_after < 0:
                raise RunnerError(
                    f"scheduler state has invalid retry_after_epoch: {state_path}"
                )
            if now_epoch < retry_after:
                os.close(lock_fd)
                return False, None, None

        state["last_attempt_epoch"] = now_epoch
        state["retry_after_epoch"] = now_epoch + args.retry_seconds
        _write_state(state_path, state)
        return True, lock_fd, state_path
    except BaseException:
        os.close(lock_fd)
        raise


def _record_success(
    state_path: Path, *, job: str, success_epoch: int, interval_seconds: int
) -> None:
    state = _read_state(state_path, job=job)
    if state is None:
        raise RunnerError(f"scheduler state disappeared before success: {state_path}")
    state["last_success_epoch"] = success_epoch
    state["next_due_epoch"] = success_epoch + interval_seconds
    state.pop("retry_after_epoch", None)
    _write_state(state_path, state)


def _wait_for_network(host: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            result = subprocess.run(
                ["/usr/sbin/scutil", "-r", host],
                capture_output=True,
                text=True,
                timeout=min(10, timeout_seconds),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if (
            result is not None
            and result.returncode == 0
            and any(line.strip() == "Reachable" for line in result.stdout.splitlines())
        ):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RunnerError(
                f"network host did not become reachable within {timeout_seconds}s: {host}"
            )
        time.sleep(min(5, remaining))


def run(argv: Sequence[str]) -> int:
    args, command = parse_args(argv)
    lock_fd: int | None = None
    state_path: Path | None = None
    if args.schedule_kind == "interval":
        current_boot_id, boot_epoch = boot_metadata()
        due, lock_fd, state_path = _prepare_due_interval(
            args,
            now_epoch=int(time.time()),
            current_boot_id=current_boot_id,
            boot_epoch=boot_epoch,
        )
        if not due:
            return 0

    try:
        if args.jitter_seconds:
            time.sleep(random.SystemRandom().randrange(args.jitter_seconds + 1))
        if args.network_host:
            _wait_for_network(args.network_host, args.network_wait_seconds)
        completed = subprocess.run(command, check=False)
        if completed.returncode == 0 and state_path is not None:
            assert args.interval_seconds is not None
            _record_success(
                state_path,
                job=args.job,
                success_epoch=int(time.time()),
                interval_seconds=args.interval_seconds,
            )
        return completed.returncode
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
