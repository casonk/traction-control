#!/usr/bin/env python3
"""Set each repository's CODECOV_TOKEN GitHub secret from Codecov, using a PAT in the vault.

The only credential kept in the auto-pass vault is a Codecov user API token (a personal
access token): the entry ``codecov/codecov-pat-user`` in the ``infra`` vault. For each
repository this tool asks Codecov's API for that repository's upload token and sets the
repository's ``CODECOV_TOKEN`` Actions secret with it, so no upload token is ever typed,
stored in a file or shown.

Commands:

- ``status``: whether the PAT can be read, and for each ``--repo`` whether Codecov can
  see it, whether it has received coverage yet, and whether the GitHub secret is set.
  Values are never shown.
- ``push``: fetch each upload token and set the GitHub secret. ``--only-missing`` leaves a
  secret that is already set; ``--dry-run`` still fetches the token (a read) to prove it
  works but sets nothing.

Neither token appears in any argument list or message: the PAT travels only in an HTTP
header, the upload token only on the standard input of ``gh secret set``, and every message
is scrubbed of both. The PAT is read through the auto-pass provisioning daemon, whose
allowlist must permit this repository to read exactly that entry (auto-pass
``provisioning-allowlist.local.toml``); the daemon infers the caller from the working
directory, so the tool runs it from this repository.

Repository names are not listed here: pass them with ``--repo``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT = "infra"
PAT_ENTRY = "codecov/codecov-pat-user"
SECRET_NAME = "CODECOV_TOKEN"
DEFAULT_OWNER = "casonk"
API = "https://api.codecov.io/api/v2/github"
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TOKEN_SHAPE = re.compile(r"^\S{16,128}$")  # one run of non-space characters, not a sentence

Runner = Callable[[list[str], str | None], "subprocess.CompletedProcess[str]"]
Http = Callable[[str, str], "tuple[int, Any]"]  # (url, bearer) -> (status, decoded JSON or None)


class CodecovSecretsError(Exception):
    """A failure to report in a sentence, not a traceback."""


def default_runner(command: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, input=stdin, capture_output=True, text=True, timeout=60, check=False, cwd=REPO_ROOT
    )


def default_http(url: str, bearer: str) -> tuple[int, Any]:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https host
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return 0, None


def scrub(text: str, *secrets: str | None) -> str:
    """Remove every known secret from text that may be shown."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<token>")
    return text


def check_repo(repo: str) -> str:
    if not REPO_NAME.fullmatch(repo):
        raise CodecovSecretsError(f"not a usable repository name: {repo!r}")
    return repo


@dataclass(frozen=True)
class Pat:
    state: str  # "present", "missing", "denied", "locked" or "error"
    token: str | None = None
    detail: str = ""


def read_pat(run: Runner) -> Pat:
    result = run(["auto-pass", "provision-get", PAT_ENTRY, "--db", VAULT], None)
    if result.returncode == 0:
        token = result.stdout.strip()
        if not TOKEN_SHAPE.fullmatch(token):
            return Pat("error", None, "the vault entry does not look like a token (empty or has spaces)")
        return Pat("present", token)
    message = (result.stderr or result.stdout).lower()
    if "not permitted" in message:
        return Pat("denied", None, "the allowlist does not let this repository read that entry")
    if "could not find entry" in message:
        return Pat("missing", None, f"no vault entry {PAT_ENTRY} in the {VAULT} vault")
    if "locked" in message or "unlock" in message:
        return Pat("locked", None, "the provisioning daemon is locked: run `auto-pass unlock`")
    return Pat("error", None, "auto-pass failed; run `auto-pass daemon-status`")


def codecov_get(owner: str, repo: str, suffix: str, pat: str, http: Http) -> tuple[int, Any]:
    return http(f"{API}/{owner}/repos/{repo}/{suffix}", pat)


def explain(status: int) -> str:
    return {
        0: "Codecov could not be reached",
        401: "Codecov rejected the PAT (expired or revoked?)",
        403: "the PAT is not allowed to read that repository's token",
        404: "Codecov cannot see that repository: install the Codecov GitHub app on it",
    }.get(status, f"Codecov answered HTTP {status}")


def fetch_upload_token(owner: str, repo: str, pat: str, http: Http) -> str:
    status, body = codecov_get(owner, repo, "config/", pat, http)
    if status != 200:
        raise CodecovSecretsError(explain(status))
    token = (body or {}).get("upload_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not TOKEN_SHAPE.fullmatch(token):
        raise CodecovSecretsError("Codecov returned no usable upload token")
    return token


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


def status(repos: Sequence[str], owner: str, run: Runner, http: Http) -> list[str]:
    pat = read_pat(run)
    lines = [f"Codecov PAT ({VAULT}/{PAT_ENTRY}): {pat.state}" + (f" ({pat.detail})" if pat.detail else "")]
    for repo in repos:
        if pat.token is None:
            seen = "unknown (no PAT)"
        else:
            code, body = codecov_get(owner, repo, "", pat.token, http)
            if code == 200 and isinstance(body, dict):
                seen = "has coverage" if body.get("active") else "visible, no coverage yet"
            else:
                seen = explain(code)
        github = {True: "set", False: "not set", None: "unknown"}[secret_is_set(owner, repo, run)]
        lines.append(f"{repo:<28} Codecov: {seen}; GitHub secret: {github}")
    return lines


def push(
    repos: Sequence[str], owner: str, run: Runner, http: Http, *, only_missing: bool, dry_run: bool
) -> tuple[list[str], int]:
    pat = read_pat(run)
    if pat.token is None:
        return [f"FAIL: Codecov PAT {pat.state}: {pat.detail}"], 1
    report, failures = [], 0
    for repo in repos:
        try:
            if only_missing and secret_is_set(owner, repo, run):
                report.append(f"skip {repo}: GitHub secret already set")
                continue
            token = fetch_upload_token(owner, repo, pat.token, http)
            if dry_run:
                report.append(f"would set {SECRET_NAME} on {owner}/{repo} (upload token fetched, not stored)")
                continue
            try:
                set_secret(owner, repo, token, run)
            except CodecovSecretsError as exc:
                raise CodecovSecretsError(scrub(str(exc), token, pat.token)) from exc
            report.append(f"set {SECRET_NAME} on {owner}/{repo}")
        except CodecovSecretsError as exc:
            report.append(f"FAIL {repo}: {scrub(str(exc), pat.token)}")
            failures += 1
    return report, failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "push"))
    parser.add_argument("--repo", action="append", required=True, metavar="NAME",
                        help="Repository name, without the owner (repeatable).")
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--only-missing", action="store_true", help="push: leave secrets that are already set.")
    parser.add_argument("--dry-run", action="store_true", help="push: fetch but set nothing.")
    return parser


def main(argv: Sequence[str] | None = None, run: Runner = default_runner, http: Http = default_http) -> int:
    args = _parser().parse_args(argv)
    try:
        for repo in args.repo:
            check_repo(repo)  # validate every name before any call
        if args.command == "status":
            print("\n".join(status(args.repo, args.owner, run, http)))
            return 0
        report, failures = push(
            args.repo, args.owner, run, http, only_missing=args.only_missing, dry_run=args.dry_run
        )
        print("\n".join(report))
        return 1 if failures else 0
    except CodecovSecretsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
