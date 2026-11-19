#!/usr/bin/env python3
"""Encrypted, verified, versioned backups of Git-ignored datasets onto a spinning disk.

For data too large for the config store (databases, document archives, vaults): each dataset in
``config/state-datasets.local.toml`` becomes one ``tar.zst`` stream, encrypted with age to the portfolio's public recipients,
written to an HDD, checked, and the oldest generations beyond ``keep`` are removed. SQLite files are copied with the
SQLite backup API (a consistent snapshot, never a raw copy of a live file). A dataset whose files have not changed since
its newest generation is skipped.

Subcommands: ``plan``, ``backup [NAME...]``, ``verify``, ``status``, ``restore``.

Why not the portfolio sidecar? Its schema accepts only registry-public repositories and its targets are SFTP-only, so it
cannot hold these private datasets or write to a local disk. Why age and not restic? restic is not installed here; this
format needs only age and zstd, and every generation is a self-contained file, so there is nothing to corrupt but one file.

The destination must be a spinning disk (docs/bulk-storage.md); a flash destination is refused unless ``--allow-flash``.
Only archive metadata (counts, sizes, a fingerprint, the ciphertext checksum) is stored in plaintext, never file names.
Decrypting needs the age identity, which this script reads only to verify and restore.
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_bulk_storage import disk_kind  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PORTFOLIO_ROOT = REPO.parent.parent
CONFIG = Path(os.environ.get("STATE_DATASETS_CONFIG", REPO / "config" / "state-datasets.local.toml"))
DEFAULT_DEST = "/mnt/16tb-sata/portfolio-backups/state"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class BackupError(RuntimeError):
    pass


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise BackupError(f"no dataset config at {path}; copy config/state-datasets.toml.example there")
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for ds in cfg.get("dataset", []):
        if not NAME_RE.match(ds.get("name", "")):
            raise BackupError(f"invalid dataset name: {ds.get('name')!r}")
        if ds["name"] in seen:
            raise BackupError(f"duplicate dataset: {ds['name']}")
        seen.add(ds["name"])
        if not ds.get("paths") and not ds.get("globs"):
            raise BackupError(f"dataset {ds['name']} has no paths or globs")
    return cfg


def recipients(cfg: dict) -> list[str]:
    if cfg.get("recipients"):
        return list(cfg["recipients"])
    if not cfg.get("recipients_from"):
        raise BackupError("set recipients, or recipients_from (a TOML file with [scopes.global] recipients), in the dataset config")
    src = resolve(cfg["recipients_from"])
    try:
        found = tomllib.loads(src.read_text(encoding="utf-8"))["scopes"]["global"]["recipients"]
    except (OSError, KeyError, ValueError) as exc:
        raise BackupError(f"no recipients readable from {src}: {exc}") from exc
    if not found:
        raise BackupError("no age recipients available")
    return list(found)


def _identity(cfg: dict) -> str:
    if not cfg.get("identity"):
        raise BackupError("set identity (the age identity file used to verify and restore) in the dataset config")
    return cfg["identity"]


def resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else PORTFOLIO_ROOT / p


def collect(ds: dict) -> tuple[list[tuple[str, Path]], set[Path]]:
    """Regular files of a dataset as (archive name, source), and the set of SQLite sources among them."""
    excludes = ds.get("exclude", [])
    sqlite_paths = {resolve(p) for p in ds.get("sqlite", [])}
    files: dict[str, Path] = {}
    bases = [resolve(raw) for raw in ds.get("paths", [])]
    for pattern in ds.get("globs", []):
        bases += sorted(PORTFOLIO_ROOT.glob(pattern))
    for base in bases:
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else [Path(d, f) for d, _ds, fs in os.walk(base) for f in fs]
        for f in candidates:
            name = f.as_posix().lstrip("/")
            if any(fnmatch.fnmatch(name, e) or fnmatch.fnmatch(f.name, e) for e in excludes):
                continue
            if f.name.endswith(("-wal", "-shm")) and f.with_name(re.sub(r"-(wal|shm)$", "", f.name)) in sqlite_paths:
                continue  # the SQLite snapshot already includes the WAL's content
            try:
                st = f.lstat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                files[name] = f
    return sorted(files.items()), sqlite_paths


def fingerprint(files: list[tuple[str, Path]]) -> str:
    h = hashlib.sha256()
    for name, path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def sqlite_snapshot(src: Path, dst: Path) -> None:
    """A consistent copy through SQLite's own backup API, opened read-only."""
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source, sqlite3.connect(dst) as target:
        source.backup(target)


def generations(dest: Path, name: str) -> list[Path]:
    return sorted(dest.glob(f"{name}-*.tar.zst.age"))


def _meta(archive: Path) -> dict:
    try:
        return json.loads(archive.with_name(archive.name + ".json").read_text())
    except (OSError, ValueError):
        return {}


def write_archive(files, sqlite_paths, output: Path, recips: list[str]) -> tuple[str, int]:
    """Stream files -> tar -> zstd -> age -> output. Returns (ciphertext sha256, ciphertext bytes)."""
    age_cmd = ["age"] + [x for r in recips for x in ("-r", r)]
    zstd = subprocess.Popen(["zstd", "-T0", "-q", "-3"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    age = subprocess.Popen(age_cmd, stdin=zstd.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    zstd.stdout.close()  # type: ignore[union-attr]
    digest = hashlib.sha256()
    written = [0]

    def drain() -> None:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            for chunk in iter(lambda: age.stdout.read(1 << 20), b""):  # type: ignore[union-attr]
                out.write(chunk)
                digest.update(chunk)
                written[0] += len(chunk)

    t = threading.Thread(target=drain)
    t.start()
    try:
        with tempfile.TemporaryDirectory(prefix="state-backup-") as tmp, tarfile.open(fileobj=zstd.stdin, mode="w|") as tar:
            for name, path in files:
                source = path
                if path in sqlite_paths:
                    source = Path(tmp, hashlib.sha256(name.encode()).hexdigest())
                    sqlite_snapshot(path, source)
                try:
                    info = tar.gettarinfo(str(source), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with source.open("rb") as fh:
                        tar.addfile(info, fh)
                except OSError:
                    continue  # vanished while backing up
    finally:
        zstd.stdin.close()  # type: ignore[union-attr]
        t.join()
    zrc, arc = zstd.wait(), age.wait()
    if zrc or arc:
        err = age.stderr.read().decode(errors="replace").strip()[:300] if age.stderr else ""
        output.unlink(missing_ok=True)
        raise BackupError(f"encryption pipeline failed (zstd {zrc}, age {arc}): {err}")
    return digest.hexdigest(), written[0]


def decrypt_stream(archive: Path, identity: Path):
    """Yield a tar read-stream of the decrypted archive. Caller must consume it and call .close()."""
    age = subprocess.Popen(["age", "-d", "-i", str(identity), str(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    zstd = subprocess.Popen(["zstd", "-dc", "-q"], stdin=age.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    age.stdout.close()  # type: ignore[union-attr]
    return age, zstd


def verify_archive(archive: Path, identity: Path, expect_files: int | None) -> int:
    """Checksum, then decrypt and read the whole archive. Returns the member count."""
    meta = _meta(archive)
    if meta.get("sha256"):
        h = hashlib.sha256()
        with archive.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != meta["sha256"]:
            raise BackupError(f"{archive.name}: ciphertext checksum mismatch")
    age, zstd = decrypt_stream(archive, identity)
    count = 0
    try:
        with tarfile.open(fileobj=zstd.stdout, mode="r|") as tar:
            for m in tar:
                if m.isreg():
                    fh = tar.extractfile(m)
                    while fh is not None and fh.read(1 << 20):
                        pass
                    count += 1
    finally:
        zstd.stdout.close()  # type: ignore[union-attr]
        arc, zrc = age.wait(), zstd.wait()
        for pipe in (age.stderr, zstd.stderr):
            if pipe:
                pipe.close()
    if arc or zrc:
        raise BackupError(f"{archive.name}: decryption failed (age {arc}, zstd {zrc})")
    if expect_files is not None and count != expect_files:
        raise BackupError(f"{archive.name}: archive holds {count} files, expected {expect_files}")
    return count


def check_destination(dest: Path, allow_flash: bool) -> None:
    if subprocess.run(["git", "-C", str(dest if dest.exists() else dest.parent), "rev-parse", "--git-dir"],
                      capture_output=True).returncode == 0:
        raise BackupError(f"{dest} is inside a Git working tree; backups must live outside every repository")
    kind = disk_kind(str(dest))
    if kind != "hdd" and not allow_flash:
        raise BackupError(f"{dest} is on {kind} storage, not a spinning disk; bulk backups belong on an HDD "
                          "(docs/bulk-storage.md), or pass --allow-flash")


def backup_dataset(ds: dict, dest: Path, recips: list[str], identity: Path, keep: int, *, force: bool, verify: bool) -> str:
    name = ds["name"]
    files, sqlite_paths = collect(ds)
    if not files:
        return f"{name}: nothing to back up (paths missing or empty)"
    fp = fingerprint(files)
    existing = generations(dest, name)
    if existing and not force and _meta(existing[-1]).get("fingerprint") == fp:
        return f"{name}: unchanged since {existing[-1].name}"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = dest / f"{name}-{stamp}.tar.zst.age"
    sha, size = write_archive(files, sqlite_paths, out, recips)
    meta = {"name": name, "created": int(time.time()), "files": len(files), "source_bytes": sum(p.stat().st_size for _, p in files if p.exists()),
            "bytes": size, "sha256": sha, "fingerprint": fp, "sqlite": len(sqlite_paths), "verified": False}
    meta_path = out.with_name(out.name + ".json")
    meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    os.chmod(meta_path, 0o600)
    if verify:
        try:
            verify_archive(out, identity, None)  # members can legitimately vanish mid-run, so the count is advisory
        except BackupError:
            out.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            raise
        meta["verified"] = True
        meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
        for old in generations(dest, name)[:-keep]:
            old.unlink(missing_ok=True)
            old.with_name(old.name + ".json").unlink(missing_ok=True)
    return f"{name}: wrote {out.name} ({len(files)} files, {size} bytes{', verified' if meta['verified'] else ''})"


def cmd_backup(cfg: dict, args) -> int:
    dest = Path(cfg.get("dest", DEFAULT_DEST)).expanduser()
    check_destination(dest, args.allow_flash)
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    recips = recipients(cfg)
    identity = Path(_identity(cfg)).expanduser()
    if not args.no_verify and not identity.is_file():
        raise BackupError(f"identity {identity} not found; cannot verify (use --no-verify to skip, not recommended)")
    keep = int(cfg.get("keep", 8))
    wanted = set(args.names)
    datasets = [d for d in cfg.get("dataset", []) if not wanted or d["name"] in wanted]
    if wanted - {d["name"] for d in datasets}:
        raise BackupError(f"unknown dataset(s): {sorted(wanted - {d['name'] for d in datasets})}")
    with (dest / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BackupError("another backup is running") from exc
        failed = 0
        for ds in datasets:
            try:
                print(backup_dataset(ds, dest, recips, identity, keep, force=args.force, verify=not args.no_verify))
            except (BackupError, OSError, sqlite3.Error) as exc:
                failed += 1
                print(f"{ds['name']}: FAILED: {exc}", file=sys.stderr)
    return 1 if failed else 0


def cmd_plan(cfg: dict, _args) -> int:
    dest = Path(cfg.get("dest", DEFAULT_DEST)).expanduser()
    print(f"destination {dest} ({disk_kind(str(dest))})")
    for ds in cfg.get("dataset", []):
        files, sq = collect(ds)
        size = sum(p.stat().st_size for _, p in files if p.exists())
        prior = generations(dest, ds["name"]) if dest.exists() else []
        changed = not prior or _meta(prior[-1]).get("fingerprint") != fingerprint(files)
        print(f"  {ds['name']:<28} {len(files):>7} files {size / 1e6:>10.1f} MB  sqlite {len(sq)}  {'CHANGED' if changed else 'unchanged'}")
    return 0


def cmd_status(cfg: dict, args) -> int:
    dest = Path(cfg.get("dest", DEFAULT_DEST)).expanduser()
    bad = 0
    for ds in cfg.get("dataset", []):
        gens = generations(dest, ds["name"]) if dest.exists() else []
        if not gens:
            print(f"{ds['name']}: no backup")
            bad += 1
            continue
        meta = _meta(gens[-1])
        age_days = (time.time() - meta.get("created", 0)) / 86400
        flag = ""
        if not meta.get("verified"):
            flag = " NOT VERIFIED"
            bad += 1
        if args.max_age_days is not None and age_days > args.max_age_days:
            flag += f" STALE (> {args.max_age_days} days)"
            bad += 1
        print(f"{ds['name']}: {len(gens)} generation(s), newest {age_days:.1f} days old{flag}")
    return 1 if bad else 0


def cmd_verify(cfg: dict, args) -> int:
    dest = Path(cfg.get("dest", DEFAULT_DEST)).expanduser()
    identity = Path(_identity(cfg)).expanduser()
    bad = 0
    for ds in cfg.get("dataset", []):
        if args.names and ds["name"] not in args.names:
            continue
        gens = generations(dest, ds["name"])
        if not gens:
            print(f"{ds['name']}: no backup")
            bad += 1
            continue
        try:
            n = verify_archive(gens[-1], identity, _meta(gens[-1]).get("files"))
            print(f"{ds['name']}: {gens[-1].name} OK ({n} files)")
        except BackupError as exc:
            bad += 1
            print(f"{ds['name']}: FAILED: {exc}", file=sys.stderr)
    return 1 if bad else 0


def cmd_restore(cfg: dict, args) -> int:
    dest = Path(cfg.get("dest", DEFAULT_DEST)).expanduser()
    identity = Path(_identity(cfg)).expanduser()
    gens = generations(dest, args.name)
    if args.generation:
        gens = [g for g in gens if args.generation in g.name]
    if not gens:
        raise BackupError(f"no matching generation of {args.name}")
    target = Path(args.to)
    if target.exists() and any(target.iterdir()):
        raise BackupError(f"{target} is not empty; restore only into a new directory")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    age, zstd = decrypt_stream(gens[-1], identity)
    try:
        with tarfile.open(fileobj=zstd.stdout, mode="r|") as tar:
            tar.extractall(target, filter="data")
    finally:
        zstd.stdout.close()  # type: ignore[union-attr]
        age.wait()
        zstd.wait()
    print(f"restored {gens[-1].name} into {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    b = sub.add_parser("backup")
    b.add_argument("names", nargs="*")
    b.add_argument("--force", action="store_true")
    b.add_argument("--no-verify", action="store_true")
    b.add_argument("--allow-flash", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("names", nargs="*")
    s = sub.add_parser("status")
    s.add_argument("--max-age-days", type=float)
    r = sub.add_parser("restore")
    r.add_argument("name")
    r.add_argument("--to", required=True)
    r.add_argument("--generation", help="substring of the generation file name (default: newest)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        return {"plan": cmd_plan, "backup": cmd_backup, "verify": cmd_verify, "status": cmd_status, "restore": cmd_restore}[args.cmd](cfg, args)
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
