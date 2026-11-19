#!/usr/bin/env python3
"""Bundle every Git-ignored local-only file in the portfolio into one deterministic archive, and back it up.

Every repository keeps its real settings in ignored ``*.local.*`` / ``.env`` files, plus a few keys and state files
live outside any repository (SSH and GPG keys, Terraform state). Nothing else backs them up. This script discovers them
(so a new file is covered with no declaration), writes them to one reproducible ``tar.gz``, and ``backup`` hands that
archive to the private encrypted-config store, which encrypts it to age recipients and commits the ciphertext to a
dedicated branch of that private repository: a small, encrypted, off-machine copy. The store's location is machine-local
configuration (``store_repo`` and ``store_script`` in ``config/local-state.local.toml``), not tracked here.

Subcommands:

  status    count what would be bundled; with --max-age-days, fail when the last successful backup is stale
  build     write the archive (deterministic: the same files give the same bytes)
  list      list an archive's contents
  restore   extract an archive back to its paths (dry run unless --apply; never overwrites without --force)
  backup    build, and if the content changed, capture it with the store and push the encrypted payload

The archive is plaintext on disk. It is written owner-only under the state directory and only ever leaves this machine
encrypted by the store. Optional, ignored extras live in ``config/local-state.local.toml``
(see ``config/local-state.toml.example``).
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_PORTFOLIO_ROOT = REPO.parent.parent
STATE_DIR = Path(os.environ.get("TRACTION_CONTROL_STATE_DIR", "~/.local/share/traction-control")).expanduser() / "local-state"
BUNDLE_NAME = "local-state-bundle.tar.gz"
STATE_FILE = "state.json"
CONFIG = Path(os.environ.get("LOCAL_STATE_CONFIG", REPO / "config" / "local-state.local.toml"))
ENTRY_ID = "local-state-bundle"
BRANCH = "local-state-backups"
MAX_FILE_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 900 * 1024  # the store refuses a source over 1 MiB

SKIP_DIR_NAMES = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".terraform", "archive-repos", "vendor", ".tox", "site-packages", "build", "dist",
}
# Names that mark a file as local-only configuration or credentials.
LOCAL_PATTERNS = ("*.local.*", "*.local", ".env", ".env.*", "*.env.local", "REFS-LOCAL.md", "*.key", "*.pem")
EXAMPLE_MARKERS = (".example", ".sample", ".template", ".dist")
# Out-of-repository state worth keeping, relative to $HOME. Missing paths are skipped.
HOME_DEFAULTS = (".ssh", ".gnupg", ".config/traction-control", ".config/clockwork", ".config/auto-pass", ".kube/config")
HOME_SKIP_NAMES = ("S.*", "random_seed", "*.lock", "known_hosts.old", "agent*")
# Terraform state and the bake manifests under the traction-control state directory.
STATE_GLOBS = ("**/terraform.tfstate", "**/terraform.tfstate.backup", "**/manifests/*", "**/*version.txt")
STATE_TREE = ".local/share/traction-control"
STATE_TREE_SKIP = ("local-state", "store-state", "swa-client", "theme-writer", "actions-switch")


class BundleError(RuntimeError):
    pass


def _is_example(name: str) -> bool:
    return any(m in name for m in EXAMPLE_MARKERS)


def _matches_local(name: str) -> bool:
    return not _is_example(name) and any(fnmatch.fnmatch(name, p) for p in LOCAL_PATTERNS)


def _git_ignored(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-o", "-i", "--exclude-standard", "-z"],
        capture_output=True, check=False,
    ).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def find_repos(root: Path) -> list[Path]:
    repos = []
    for dirpath, dirnames, _files in os.walk(root):
        depth = Path(dirpath).relative_to(root).parts
        if ".git" in dirnames:
            repos.append(Path(dirpath))
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and len(depth) < 4]
    return sorted(repos)


def load_extras(path: Path) -> dict:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _walk_files(base: Path, skip: tuple[str, ...] = ()) -> list[Path]:
    if base.is_file():
        return [base]
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for name in filenames:
            if any(fnmatch.fnmatch(name, s) for s in skip):
                continue
            found.append(Path(dirpath, name))
    return found


def discover(portfolio_root: Path, home: Path, extras: dict | None = None) -> tuple[dict[str, Path], list[str]]:
    """Return ({archive name: source path}, [skipped reasons]). Archive names are ``portfolio/...`` or ``home/...``."""
    extras = extras or {}
    files: dict[str, Path] = {}
    skipped: list[str] = []

    def add(name: str, path: Path) -> None:
        try:
            st = path.lstat()
        except OSError:
            return
        if not stat.S_ISREG(st.st_mode):
            skipped.append(f"{name}: not a regular file")
        elif not os.access(path, os.R_OK):
            skipped.append(f"{name}: not readable by this account")
        elif st.st_size > MAX_FILE_BYTES:
            skipped.append(f"{name}: {st.st_size} bytes exceeds {MAX_FILE_BYTES}")
        else:
            files[name] = path

    for repo in find_repos(portfolio_root):
        rel_repo = repo.relative_to(portfolio_root)
        for rel in _git_ignored(repo):
            parts = Path(rel).parts
            if any(p in SKIP_DIR_NAMES or p.endswith(".egg-info") for p in parts):
                continue
            if _matches_local(parts[-1]) or "secrets" in parts[:-1]:
                add(f"portfolio/{rel_repo / rel}", repo / rel)

    skip_names = HOME_SKIP_NAMES
    for rel in list(HOME_DEFAULTS) + list(extras.get("home_paths", [])):
        for f in _walk_files(home / rel, skip_names):
            add(f"home/{f.relative_to(home)}", f)

    tree = home / STATE_TREE
    if tree.is_dir():
        for pattern in STATE_GLOBS:
            for f in tree.glob(pattern):
                if f.is_file() and not any(part in STATE_TREE_SKIP for part in f.relative_to(tree).parts):
                    add(f"home/{f.relative_to(home)}", f)

    for extra in extras.get("extra_files", []):
        p = Path(extra).expanduser()
        if p.is_file():
            add(f"home/{p.relative_to(home)}" if p.is_relative_to(home) else f"abs/{p.as_posix().lstrip('/')}", p)
    return dict(sorted(files.items())), sorted(skipped)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_archive(files: dict[str, Path], output: Path) -> dict:
    """Write a deterministic tar.gz (sorted, zeroed times and owners) and return its manifest."""
    manifest = {"version": 1, "files": {n: {"sha256": _sha256(p), "bytes": p.stat().st_size,
                                            "mode": f"{stat.S_IMODE(p.stat().st_mode):04o}"} for n, p in files.items()}}
    mbytes = (json.dumps(manifest, sort_keys=True, indent=1) + "\n").encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("MANIFEST.json")
        info.size, info.mode = len(mbytes), 0o600
        tar.addfile(info, io.BytesIO(mbytes))
        for name, path in files.items():
            info = tarfile.TarInfo(name)
            data = path.read_bytes()
            info.size, info.mode = len(data), stat.S_IMODE(path.stat().st_mode)
            tar.addfile(info, io.BytesIO(data))
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".partial")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    os.replace(tmp, output)
    return manifest


def read_archive(archive: Path) -> tuple[dict, dict[str, bytes], dict[str, int]]:
    """Safely read an archive: only regular files, no absolute or traversing names. Returns manifest, data, modes."""
    data: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            if not m.isreg():
                raise BundleError(f"refusing non-regular member: {m.name}")
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts:
                raise BundleError(f"refusing unsafe member name: {m.name}")
            data[m.name] = tar.extractfile(m).read()  # type: ignore[union-attr]
            modes[m.name] = m.mode
    if "MANIFEST.json" not in data:
        raise BundleError("archive has no MANIFEST.json")
    manifest = json.loads(data.pop("MANIFEST.json"))
    for name, meta in manifest["files"].items():
        if hashlib.sha256(data.get(name, b"")).hexdigest() != meta["sha256"]:
            raise BundleError(f"checksum mismatch for {name}")
    extra = set(data) - set(manifest["files"])
    if extra:
        raise BundleError(f"members not in the manifest: {sorted(extra)[:3]}")
    return manifest, data, modes


def destination(name: str, portfolio_root: Path, home: Path) -> Path:
    head, _, rest = name.partition("/")
    if head == "portfolio":
        return portfolio_root / rest
    if head == "home":
        return home / rest
    if head == "abs":
        return Path("/") / rest
    raise BundleError(f"unknown archive root in {name}")


def restore(archive: Path, portfolio_root: Path, home: Path, *, apply: bool, force: bool) -> list[str]:
    _manifest, data, modes = read_archive(archive)
    actions = []
    for name in sorted(data):
        dest = destination(name, portfolio_root, home)
        if dest.exists() and not force:
            same = dest.read_bytes() == data[name]
            actions.append(f"{'same   ' if same else 'EXISTS '} {dest}")
            continue
        actions.append(f"{'write  ' if apply else 'would  '} {dest}")
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data[name])
            os.chmod(dest, modes[name] & 0o7777)
    return actions


# ── backup through store ──────────────────────────────────────────────────

def resolve_path(path: str, portfolio_root: Path) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else portfolio_root / p


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *args])


def _state_path() -> Path:
    return STATE_DIR / STATE_FILE


def read_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, _state_path())


def ensure_worktree(store: Path, worktree: Path) -> None:
    """A checkout of the store repository on the dedicated backup branch, separate from the working checkout."""
    if (worktree / ".git").exists():
        return
    _git(store, "fetch", "-q", "origin")
    base = "origin/main" if _git(store, "rev-parse", "--verify", "-q", "origin/main").returncode == 0 else "main"
    have_remote = _git(store, "rev-parse", "--verify", "-q", f"origin/{BRANCH}").returncode == 0
    start = f"origin/{BRANCH}" if have_remote else base
    r = _git(store, "worktree", "add", "-q", "-B", BRANCH, str(worktree), start)
    if r.returncode:
        raise BundleError(f"could not create the backup worktree: {r.stderr.strip()}")


def backup(portfolio_root: Path, home: Path, store: Path, worktree: Path, store_script: str, *, force: bool, push: bool) -> dict:
    if not store_script:
        raise BundleError("no store configured: set store_repo and store_script in config/local-state.local.toml")
    extras = load_extras(CONFIG)
    files, skipped = discover(portfolio_root, home, extras)
    if not files:
        raise BundleError("nothing to back up")
    bundle = STATE_DIR / BUNDLE_NAME
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = build_archive(files, bundle)
    size = bundle.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise BundleError(f"bundle is {size} bytes; store accepts under {MAX_BUNDLE_BYTES}. Trim extras or MAX_FILE_BYTES")
    digest = _sha256(bundle)
    state = read_state()
    if digest == state.get("bundle_sha256") and not force:
        state["last_check"] = int(time.time())
        write_state(state)
        return {"status": "unchanged", "files": len(files), "skipped": skipped}

    ensure_worktree(store, worktree)
    if _git(worktree, "status", "--porcelain").stdout.strip():
        raise BundleError("the backup worktree has uncommitted changes; resolve them before the next run")
    _git(store, "fetch", "-q", "origin")
    merge = _git(worktree, "merge", "--no-edit", "-X", "theirs", "origin/main")
    if merge.returncode:
        _git(worktree, "merge", "--abort")
        raise BundleError(f"merging origin/main into the backup branch failed: {merge.stderr.strip()[:300]}")

    env = {**os.environ, **{k: str(portfolio_root) for k in extras.get("store_root_env", [])}}
    cap = _run([sys.executable, str(worktree / store_script), "capture", "--id", ENTRY_ID], env=env, cwd=worktree)
    if cap.returncode:
        raise BundleError(f"store capture failed: {(cap.stderr or cap.stdout).strip()[:400]}")
    _git(worktree, "add", "-A")
    if not _git(worktree, "status", "--porcelain").stdout.strip():
        raise BundleError("capture changed nothing in the backup worktree")
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    commit = _git(worktree, "commit", "-q", "-m", f"chore: back up local state bundle ({stamp})")
    if commit.returncode:
        raise BundleError(f"commit failed: {(commit.stderr or commit.stdout).strip()[:400]}")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    pushed = False
    if push:
        p = _git(worktree, "push", "-q", "-u", "origin", BRANCH)
        if p.returncode:
            raise BundleError(f"push failed (the local commit {head[:9]} is kept): {p.stderr.strip()[:300]}")
        pushed = True
    now = int(time.time())
    write_state({
        "bundle_sha256": digest, "bundle_bytes": size, "files": len(files), "commit": head, "pushed": pushed,
        "last_success": now, "last_check": now,
    })
    return {"status": "backed-up", "files": len(files), "bytes": size, "commit": head[:9], "pushed": pushed, "skipped": skipped}


def status_exit(max_age_days: float | None) -> int:
    state = read_state()
    if max_age_days is None:
        return 0
    last = state.get("last_success")
    if not last:
        print("local state has never been backed up")
        return 1
    age = (time.time() - last) / 86400
    if age > max_age_days:
        print(f"last successful local-state backup was {age:.1f} days ago (limit {max_age_days})")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--portfolio-root", type=Path, default=Path(os.environ.get("PORTFOLIO_ROOT", DEFAULT_PORTFOLIO_ROOT)))
    ap.add_argument("--home", type=Path, default=Path.home())
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--max-age-days", type=float)
    st.add_argument("--list", action="store_true", help="print every bundled name")
    b = sub.add_parser("build")
    b.add_argument("--output", type=Path, default=STATE_DIR / BUNDLE_NAME)
    ls = sub.add_parser("list")
    ls.add_argument("archive", type=Path)
    rs = sub.add_parser("restore")
    rs.add_argument("archive", type=Path)
    rs.add_argument("--apply", action="store_true")
    rs.add_argument("--force", action="store_true", help="overwrite files that already exist")
    bk = sub.add_parser("backup")
    bk.add_argument("--store", type=Path, help="store repository (default: store_repo from the local config)")
    bk.add_argument("--worktree", type=Path, default=STATE_DIR.parent / "store-state")
    bk.add_argument("--force", action="store_true")
    bk.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "status":
            extras = load_extras(CONFIG)
            files, skipped = discover(args.portfolio_root, args.home, extras)
            total = sum(p.stat().st_size for p in files.values())
            print(f"{len(files)} files, {total} bytes would be bundled; {len(skipped)} skipped")
            for s in skipped:
                print(f"  skipped {s}")
            if args.list:
                for n in files:
                    print(n)
            state = read_state()
            if state.get("last_success"):
                print(f"last backup: {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(state['last_success']))} ({state.get('commit', '')[:9]})")
            return status_exit(args.max_age_days)
        if args.cmd == "build":
            extras = load_extras(CONFIG)
            files, _ = discover(args.portfolio_root, args.home, extras)
            manifest = build_archive(files, args.output)
            print(f"wrote {args.output} ({len(manifest['files'])} files, {args.output.stat().st_size} bytes)")
            return 0
        if args.cmd == "list":
            manifest, _d, _m = read_archive(args.archive)
            for n in sorted(manifest["files"]):
                print(n)
            return 0
        if args.cmd == "restore":
            for line in restore(args.archive, args.portfolio_root, args.home, apply=args.apply, force=args.force):
                print(line)
            if not args.apply:
                print("dry run: pass --apply to write; existing files are kept unless --force")
            return 0
        if args.cmd == "backup":
            extras = load_extras(CONFIG)
            store = args.store or (resolve_path(extras["store_repo"], args.portfolio_root) if extras.get("store_repo") else None)
            if store is None:
                raise BundleError("no store configured: set store_repo and store_script in config/local-state.local.toml")
            result = backup(args.portfolio_root, args.home, store, args.worktree, extras.get("store_script", ""),
                            force=args.force, push=not args.no_push)
            print(json.dumps(result))
            return 0
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
