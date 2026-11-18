#!/usr/bin/env python3
"""Flag configured bulk-data destinations that sit on flash instead of a spinning disk.

Bulk writers (downloads, backups, archives, dataset mirrors, model stores) belong on an HDD: they write large,
sequential, rarely-read data that wears an SSD or NVMe for no speed benefit. This is the standing rule in
docs/bulk-storage.md; the one-off guard in scripts/backup_azure_runner_images.sh is the same check.

Two sources are read, both machine-local:

  * every ``*.env.local`` / ``*.env`` file under the portfolio root, for variables whose name ends in a bulk suffix
    (``_DOWNLOAD_DIR``, ``_BACKUP_DIR`` ...). Tracked ``*.example`` files are skipped.
  * ``config/bulk-storage.local.toml`` (ignored; see ``config/bulk-storage.toml.example``) for paths that are bulk by
    declaration, plus an allowlist of paths that are deliberately on flash.

Exit status: 0 clean, 1 findings, 2 cannot run (no lsblk/findmnt). A path whose medium cannot be determined is reported
as a finding, because unknown fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

BULK_SUFFIXES = ("_DOWNLOAD_DIR", "_BACKUP_DIR", "_BACKUP_DEST", "_ARCHIVE_DIR", "_DATASET_DIR", "_MODELS_DIR")
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "archive-repos", "vendor"}


def disk_kind(path: str, *, run=subprocess.run) -> str:
    """Return ``hdd``, ``flash`` or ``unknown`` for the device that holds ``path`` (or its nearest existing parent)."""
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    p = p.resolve()
    try:
        src = run(["findmnt", "-no", "SOURCE", "-T", str(p)], capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return "unknown"
    src = src.split("[", 1)[0]  # btrfs reports /dev/xxx[/subvolume]
    if not src.startswith("/dev/"):
        return "unknown"
    try:
        out = run(["lsblk", "-s", "-n", "-o", "TYPE,ROTA", src], capture_output=True, text=True, check=False).stdout
    except OSError:
        return "unknown"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "disk":
            return {"1": "hdd", "0": "flash"}.get(parts[1], "unknown")
    return "unknown"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def env_candidates(root: Path) -> list[tuple[str, str, str]]:
    """(file, variable, path) for each bulk-suffixed variable with an absolute path in a local env file."""
    found: list[tuple[str, str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not (name.endswith(".env.local") or name.endswith(".env") or name == ".env"):
                continue
            file = Path(dirpath, name)
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                m = ENV_LINE.match(line)
                if not m or not m.group(1).endswith(BULK_SUFFIXES):
                    continue
                value = _unquote(m.group(2))
                if value.startswith("/"):
                    found.append((str(file), m.group(1), value))
    return found


def load_config(path: Path) -> tuple[list[str], set[str]]:
    if not path.is_file():
        return [], set()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return list(data.get("bulk_paths", [])), set(data.get("allow_flash", []))


def check(root: Path, config: Path, *, kind=disk_kind) -> list[dict]:
    declared, allow = load_config(config)
    targets: list[tuple[str, str, str]] = [("config", "bulk_paths", p) for p in declared]
    targets += env_candidates(root)
    findings = []
    for source, label, path in targets:
        if path in allow:
            continue
        k = kind(path)
        if k != "hdd":
            findings.append({"source": source, "name": label, "path": path, "medium": k})
    return findings


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=here.parent.parent, help="portfolio root to scan for env files")
    ap.add_argument("--config", type=Path, default=here / "config" / "bulk-storage.local.toml")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not all(subprocess.run(["which", c], capture_output=True).returncode == 0 for c in ("findmnt", "lsblk")):
        print("error: findmnt and lsblk are required", file=sys.stderr)
        return 2
    findings = check(args.root, args.config)
    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            print(f"bulk path on {f['medium']} storage: {f['name']}={f['path']} ({Path(f['source']).name})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
