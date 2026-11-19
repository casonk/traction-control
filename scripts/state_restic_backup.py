#!/usr/bin/env python3
"""Deduplicated, encrypted, thinned backups of the ignored datasets with restic, onto a spinning disk.

Same datasets and configuration as ``state_dataset_backup.py`` (``config/state-datasets.local.toml``), a different engine:
restic stores only the changed parts of each dataset, encrypts and compresses on the client, and thins old snapshots by a
retention policy instead of keeping a fixed count. SQLite files are staged through SQLite's backup API first (a consistent
copy under a stable staging path), never read raw.

Configuration (the ``[restic]`` table of the dataset config)::

    [restic]
    repository = "/mnt/16tb-sata/portfolio-backups/restic"
    password_file = "~/.config/traction-control/restic-password"
    compression = "max"
    keep_daily = 7
    keep_weekly = 5
    keep_monthly = 12
    keep_yearly = 3

Subcommands: ``init``, ``backup [NAME...]``, ``forget``, ``check``, ``status``, ``drill NAME``, ``restore NAME --to DIR``.

The repository must be on a spinning disk and outside every Git worktree. The password file is created owner-only by
``init`` and never printed; it lives in a directory the local-state bundle backs up, which is the recovery path for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_dataset_backup as sdb  # noqa: E402
from check_bulk_storage import disk_kind  # noqa: E402

STAGE = Path(os.environ.get("TRACTION_CONTROL_STATE_DIR", "~/.local/share/traction-control")).expanduser() / "restic-stage"
DEFAULT_KEEP = {"keep_daily": 7, "keep_weekly": 5, "keep_monthly": 12, "keep_yearly": 3}


class ResticError(RuntimeError):
    pass


def settings(cfg: dict) -> dict:
    r = cfg.get("restic")
    if not r or not r.get("repository") or not r.get("password_file"):
        raise ResticError("add a [restic] table with repository and password_file to the dataset config")
    return {
        "repository": str(Path(r["repository"]).expanduser()),
        "password_file": str(Path(r["password_file"]).expanduser()),
        "compression": r.get("compression", "max"),
        **{k: int(r.get(k, v)) for k, v in DEFAULT_KEEP.items()},
    }


def env_for(s: dict) -> dict:
    return {**os.environ, "RESTIC_REPOSITORY": s["repository"], "RESTIC_PASSWORD_FILE": s["password_file"],
            "RESTIC_COMPRESSION": s["compression"], "RESTIC_CACHE_DIR": str(STAGE.parent / "restic-cache")}


def restic(s: dict, *args: str, ok: tuple[int, ...] = (0,), capture: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["restic", *args], env=env_for(s), capture_output=capture, text=True, check=False)
    if p.returncode not in ok:
        tail = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-3:]
        raise ResticError(f"restic {args[0]} failed ({p.returncode}): {' | '.join(tail)}"[:500])
    return p


def check_repository_location(repo: str, allow_flash: bool = False) -> None:
    sdb.check_destination(Path(repo), allow_flash)


def _db_key(path: Path) -> list[int]:
    """Size and mtime of a database and its WAL: a WAL-mode database can change without its main file's mtime moving."""
    key: list[int] = []
    for p in (path, path.with_name(path.name + "-wal")):
        try:
            st = p.stat()
            key += [st.st_size, st.st_mtime_ns]
        except OSError:
            key += [0, 0]
    return key


def stage_dataset(ds: dict) -> tuple[list[str], int]:
    """Return the paths restic should store (originals plus staged SQLite copies) and the SQLite count.

    A staged copy is rebuilt only when its source database changed. restic records each file's timestamps, so a fresh
    copy of an unchanged database would still make every run look like a change and defeat the deduplication."""
    files, sqlite_paths = sdb.collect(ds)
    stage = STAGE / ds["name"]
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker_path = stage / ".sources.json"
    try:
        markers = json.loads(marker_path.read_text())
    except (OSError, ValueError):
        markers = {}
    listing: list[str] = []
    for name, path in files:
        if "\n" in str(path):
            continue
        if path in sqlite_paths:
            target = stage / name
            key = _db_key(path)
            if markers.get(name) != key or not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.unlink(missing_ok=True)
                sdb.sqlite_snapshot(path, target)
                markers[name] = key
            listing.append(str(target))
        else:
            listing.append(str(path))
    marker_path.write_text(json.dumps(markers))
    return listing, len(sqlite_paths)


def cmd_init(cfg: dict, args) -> int:
    s = settings(cfg)
    check_repository_location(s["repository"], args.allow_flash)
    pw = Path(s["password_file"])
    if not pw.exists():
        pw.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(pw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(48) + "\n")
        print(f"created password file {pw} (owner-only, not printed)")
    elif pw.stat().st_mode & 0o077:
        raise ResticError(f"{pw} is readable by others; fix its mode to 0600")
    repo = Path(s["repository"])
    if (repo / "config").exists():
        print(f"repository already initialised at {repo}")
        return 0
    repo.mkdir(parents=True, exist_ok=True, mode=0o700)
    restic(s, "init", "--repository-version", "2")
    print(f"initialised restic repository (format 2, compression {s['compression']}) at {repo}")
    return 0


def backup_one(s: dict, ds: dict) -> str:
    listing, nsql = stage_dataset(ds)
    if not listing:
        return f"{ds['name']}: nothing to back up"
    with tempfile.NamedTemporaryFile("w", prefix="restic-list-", delete=False) as fh:
        fh.write("\n".join(listing) + "\n")
        list_path = fh.name
    try:
        p = restic(s, "backup", "--files-from-verbatim", list_path, "--tag", ds["name"], "--skip-if-unchanged",
                   "--group-by", "tags", "--no-scan", "--quiet", "--json", ok=(0, 3))
    finally:
        os.unlink(list_path)
    summary = {}
    for line in (p.stdout or "").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("message_type") == "summary":
            summary = obj
    note = " (some files were unreadable; snapshot is incomplete)" if p.returncode == 3 else ""
    if not summary or not summary.get("snapshot_id"):
        return f"{ds['name']}: unchanged{note}"
    return (f"{ds['name']}: snapshot {summary['snapshot_id'][:8]}, {len(listing)} files, {summary.get('data_added', 0) / 1e6:.1f} MB new "
            f"({summary.get('data_added_packed', 0) / 1e6:.1f} MB stored), {nsql} SQLite{note}")


def forget_args(s: dict) -> list[str]:
    return ["forget", "--group-by", "tags", "--keep-daily", str(s["keep_daily"]), "--keep-weekly", str(s["keep_weekly"]),
            "--keep-monthly", str(s["keep_monthly"]), "--keep-yearly", str(s["keep_yearly"])]


def cmd_backup(cfg: dict, args) -> int:
    s = settings(cfg)
    check_repository_location(s["repository"], args.allow_flash)
    if not (Path(s["repository"]) / "config").exists():
        raise ResticError("the repository is not initialised; run init first")
    wanted = set(args.names)
    datasets = [d for d in cfg.get("dataset", []) if not wanted or d["name"] in wanted]
    if wanted - {d["name"] for d in datasets}:
        raise ResticError(f"unknown dataset(s): {sorted(wanted - {d['name'] for d in datasets})}")
    failed = 0
    for ds in datasets:
        try:
            print(backup_one(s, ds))
        except (ResticError, OSError, sqlite3.Error, sdb.BackupError) as exc:
            failed += 1
            print(f"{ds['name']}: FAILED: {exc}", file=sys.stderr)
    if not args.no_forget and not failed:
        restic(s, *forget_args(s), "--prune", "--quiet")
        print("retention applied")
    return 1 if failed else 0


def cmd_forget(cfg: dict, args) -> int:
    s = settings(cfg)
    p = restic(s, *forget_args(s), *(["--dry-run"] if args.dry_run else ["--prune"]))
    print((p.stdout or "").strip()[-1500:])
    return 0


def cmd_check(cfg: dict, args) -> int:
    s = settings(cfg)
    restic(s, "check", f"--read-data-subset={args.read_data}")
    print(f"repository structure OK; {args.read_data} of the data re-read and verified")
    return 0


def snapshots(s: dict) -> list[dict]:
    return json.loads(restic(s, "snapshots", "--json").stdout or "[]")


def cmd_status(cfg: dict, args) -> int:
    s = settings(cfg)
    if not (Path(s["repository"]) / "config").exists():
        print("no restic repository")
        return 1
    snaps = snapshots(s)
    bad = 0
    for ds in cfg.get("dataset", []):
        mine = sorted((x for x in snaps if ds["name"] in x.get("tags", [])), key=lambda x: x["time"])
        if not mine:
            print(f"{ds['name']}: no backup")
            bad += 1
            continue
        stamp = mine[-1]["time"]
        # restic prints an RFC 3339 time with up to nanosecond precision; Python parses at most microseconds.
        head, _, tail = stamp.partition(".")
        zone = tail[next((i for i, c in enumerate(tail) if c in "+-Z"), len(tail)):] if tail else ""
        when = datetime.fromisoformat(head + zone.replace("Z", "+00:00") if zone else head)
        age = (datetime.now(when.tzinfo) - when).total_seconds() / 86400
        flag = ""
        if args.max_age_days is not None and age > args.max_age_days:
            flag = f" STALE (> {args.max_age_days} days)"
            bad += 1
        print(f"{ds['name']}: {len(mine)} snapshot(s), newest {age:.1f} days old{flag}")
    return 1 if bad else 0


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_restore(cfg: dict, args) -> int:
    s = settings(cfg)
    target = Path(args.to)
    if target.exists() and any(target.iterdir()):
        raise ResticError(f"{target} is not empty; restore only into a new directory")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    restic(s, "restore", "latest", "--tag", args.name, "--target", str(target), "--quiet")
    print(f"restored the newest {args.name} snapshot into {target}")
    return 0


def cmd_drill(cfg: dict, args) -> int:
    """Restore the newest snapshot into a scratch directory and compare it with the live files."""
    s = settings(cfg)
    ds = next((d for d in cfg.get("dataset", []) if d["name"] == args.name), None)
    if ds is None:
        raise ResticError(f"unknown dataset {args.name}")
    with tempfile.TemporaryDirectory(prefix="restic-drill-") as t:
        restic(s, "restore", "latest", "--tag", args.name, "--target", t, "--quiet")
        files, sqlite_paths = sdb.collect(ds)
        same = differ = missing = checked_db = 0
        for name, path in files:
            restored = Path(t, str(STAGE / args.name / name).lstrip("/")) if path in sqlite_paths else Path(t, str(path).lstrip("/"))
            if not restored.is_file():
                missing += 1
                continue
            if path in sqlite_paths:
                ok = sqlite3.connect(restored).execute("pragma integrity_check").fetchone()[0] == "ok"
                checked_db += 1
                same, differ = (same + 1, differ) if ok else (same, differ + 1)
            elif path.stat().st_size == restored.stat().st_size and _sha(path) == _sha(restored):
                same += 1
            else:
                differ += 1  # may have legitimately changed since the snapshot
    print(f"{args.name}: {same} verified, {differ} differ, {missing} missing from the snapshot, {checked_db} SQLite integrity-checked")
    return 0 if missing == 0 and differ == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=sdb.CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--allow-flash", action="store_true")
    b = sub.add_parser("backup")
    b.add_argument("names", nargs="*")
    b.add_argument("--no-forget", action="store_true")
    b.add_argument("--allow-flash", action="store_true")
    f = sub.add_parser("forget")
    f.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--read-data", default="5%", help="portion of the data to re-read, e.g. 5%% or 1/10")
    s = sub.add_parser("status")
    s.add_argument("--max-age-days", type=float)
    r = sub.add_parser("restore")
    r.add_argument("name")
    r.add_argument("--to", required=True)
    d = sub.add_parser("drill")
    d.add_argument("name")
    args = ap.parse_args(argv)
    if not shutil.which("restic"):
        print("error: restic is not installed", file=sys.stderr)
        return 2
    try:
        cfg = sdb.load_config(args.config)
        return {"init": cmd_init, "backup": cmd_backup, "forget": cmd_forget, "check": cmd_check, "status": cmd_status,
                "restore": cmd_restore, "drill": cmd_drill}[args.cmd](cfg, args)
    except (ResticError, sdb.BackupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
