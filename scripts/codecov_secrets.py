#!/usr/bin/env python3
"""Move Codecov upload tokens from the auto-pass vault into GitHub repository secrets.

Each repository's Codecov upload token lives in the vault as the entry
``dev/codecov/<repo>`` (token in the password field). This tool reads it through the
auto-pass provisioning daemon and sets that repository's ``CODECOV_TOKEN`` Actions
secret, so the token is never typed into a shell, a file or the GitHub UI.

Commands:

- ``status``: for each ``--repo``, whether the vault entry exists and whether the
  GitHub secret is set. Values are never shown.
- ``push``: set the GitHub secret from the vault entry. ``--only-missing`` leaves a
  secret that is already set alone; ``--dry-run`` reports what would happen and makes
  no change.

The token travels only on the standard input of ``gh secret set``: it is not in any
argument list, and every message is scrubbed of it before it is printed. The
provisioning allowlist (auto-pass ``provisioning-allowlist.local.toml``) must permit
this repository to read ``dev/codecov/*``; ``auto-pass provision-get`` infers the
caller from the working directory, so the tool runs it from this repository.

Repository names are not listed here: pass them with ``--repo``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_PREFIX = "dev/codecov/"
VAULT = "master"
SECRET_NAME = "CODECOV_TOKEN"
DEFAULT_OWNER = "casonk"
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TOKEN_SHAPE = re.compile(r"^\S{16,128}$")  # one run of non-space characters, not a sentence

Runner = Callable[[list[str], str | None], "subprocess.CompletedProcess[str]"]


class CodecovSecretsError(Exception):
    """A failure to report in a sentence, not a traceback."""


def default_runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, input=stdin, capture_output=True, text=True, timeout=60, check=False, cwd=REPO_ROOT
    )


def scrub(text: str, token: str | None) -> str:
    """Remove the token (if known) from text that may be shown."""
    return text.replace(token, "<token>") if token else text


def entry_path(repo: str) -> str:
    if not REPO_NAME.fullmatch(repo):
        raise CodecovSecretsError(f"not a usable repository name: {repo!r}")
    return f"{ENTRY_PREFIX}{repo}"


@dataclass(frozen=True)
class Vault:
    state: str  # "present", "missing", "denied", "locked" or "error"
    token: str | None = None
    detail: str = ""


def read_vault(repo: str, run: Runner) -> Vault:
    result = run(["auto-pass", "provision-get", entry_path(repo), "--db", VAULT], None)
    if result.returncode == 0:
        token = result.stdout.strip()
        if not TOKEN_SHAPE.fullmatch(token):
            return Vault("error", None, "the vault entry does not look like a token (empty or has spaces)")
        return Vault("present", token)
    message = (result.stderr or result.stdout).lower()
    if "not permitted" in message:
        return Vault("denied", None, "the allowlist does not let this repository read that entry")
    if "could not find entry" in message:
        return Vault("missing", None, "no vault entry yet")
    if "locked" in message or "unlock" in message:
        return Vault("locked", None, "the provisioning daemon is locked: run `auto-pass unlock`")
    return Vault("error", None, "auto-pass failed; run `auto-pass daemon-status`")


def secret_is_set(owner: str, repo: str, run: Runner) -> bool | None:
    """True or False, or None when GitHub cannot be asked."""
    result = run(["gh", "secret", "list", "-R", f"{owner}/{repo}", "--json", "name"], None)
    if result.returncode != 0:
        return None
    try:
        return any(item.get("name") == SECRET_NAME for item in json.loads(result.stdout or "[]"))
    except ValueError:
        return None


def set_secret(owner: str, repo: str, token: str, run: Runner) -> None:
    result = run(["gh", "secret", "set", SECRET_NAME, "-R", f"{owner}/{repo}"], token)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise CodecovSecretsError(scrub(lines[-1], token) if lines else "gh secret set failed")


def status(repos: Sequence[str], owner: str, run: Runner) -> list[str]:
    lines = []
    for repo in repos:
        vault = read_vault(repo, run)
        secret = secret_is_set(owner, repo, run)
        github = {True: "set", False: "not set", None: "unknown"}[secret]
        extra = f" ({vault.detail})" if vault.detail else ""
        lines.append(f"{repo:<28} vault entry: {vault.state:<8}{extra}; GitHub secret: {github}")
    return lines


def push(
    repos: Sequence[str], owner: str, run: Runner, *, only_missing: bool, dry_run: bool
) -> tuple[list[str], int]:
    report, failures = [], 0
    for repo in repos:
        vault = read_vault(repo, run)
        if vault.state != "present" or vault.token is None:
            report.append(f"FAIL {repo}: {vault.state}: {vault.detail}")
            failures += 1
            continue
        if only_missing and secret_is_set(owner, repo, run):
            report.append(f"skip {repo}: GitHub secret already set")
            continue
        if dry_run:
            report.append(f"would set {SECRET_NAME} on {owner}/{repo} from {entry_path(repo)}")
            continue
        try:
            set_secret(owner, repo, vault.token, run)
        except CodecovSecretsError as exc:
            report.append(f"FAIL {repo}: {scrub(str(exc), vault.token)}")
            failures += 1
            continue
        report.append(f"set {SECRET_NAME} on {owner}/{repo}")
    return report, failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "push"))
    parser.add_argument("--repo", action="append", required=True, metavar="NAME",
                        help="Repository name, without the owner (repeatable).")
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--only-missing", action="store_true", help="push: leave secrets that are already set.")
    parser.add_argument("--dry-run", action="store_true", help="push: report only; change nothing.")
    return parser


def main(argv: Sequence[str] | None = None, run: Runner = default_runner) -> int:
    args = _parser().parse_args(argv)
    try:
        for repo in args.repo:
            entry_path(repo)  # validate every name before any call
        if args.command == "status":
            print("\n".join(status(args.repo, args.owner, run)))
            return 0
        report, failures = push(
            args.repo, args.owner, run, only_missing=args.only_missing, dry_run=args.dry_run
        )
        print("\n".join(report))
        return 1 if failures else 0
    except CodecovSecretsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
