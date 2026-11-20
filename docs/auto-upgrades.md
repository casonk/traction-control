# Staying current automatically

Version lists, base images and OS packages go stale quietly: a CI matrix names last
year's Python, a container serves a Playwright that no consumer uses, a workstation sits
on unpatched packages for weeks. The portfolio rule is that **nothing hand-edits a
version to stay current**. Each layer below has one owner that notices a new release or
a security patch, proves it works, and rolls it out (or alerts), and every other repo
just consumes that.

## The layers

| Layer | Owner | What it does | Where |
|---|---|---|---|
| CI Python matrix | `dot-github` | `python-versions: auto` resolves to the newest three supported CPython releases within the repo's `requires-python`; `auto-edges` gives the declared floor plus the newest. | `scripts/resolve_python_versions.py`, `python-ci.yml`, `install-check.yml` |
| GitHub Actions pins | `dot-github` + Dependabot | Action versions are pinned once, in the reusable workflows, and Dependabot keeps them current. Callers do not pin actions. | `dot-github/.github/dependabot.yml` |
| Browser server (Playwright, Node, base images) | `windshield` | A daily check rotates in new Playwright and Node releases, replaces served versions with their security patches, rebuilds when a base image's digest moves, verifies every version (Node client and a Python-client matrix on the latest three CPython), then promotes, with rollback and alerts. | `windshield/src/windshield/container_rotation.py` |
| Consumer Playwright clients | `windshield` | Registered interpreters are moved to the newest served version after promotion, smoke-tested, and restored on failure. | `~/.config/windshield/consumers.txt` |
| Host OS packages and reboots | `fedora-debugg` | Scheduled `dnf` updates with a reboot only when needed, in a quiet window. | `fedora-debugg` (see its README) |

## Rules

1. **No literal Python lists in workflows.** Use `python-versions: auto` for the test
   matrix and `auto-edges` for install checks. `scripts/check_ci_python_versions.py`
   reports every repo that still carries a list; `portfolio-audit.sh` runs it.
2. **`requires-python` is a claim, so keep it honest.** `auto-edges` tests the declared
   floor even after it reaches end of life, which makes a stale floor visible. When a
   floor goes EOL, raise it (and the code that supports it) or record why it stays.
3. **Verify before promoting.** Anything that replaces a running component builds a
   candidate, exercises every supported combination (not just the newest) in a
   throwaway instance, promotes only on success, re-checks after, and restores the
   previous state if the re-check fails. A failed candidate is remembered so it is not
   rebuilt every day; `--force` retries.
4. **Security patches and new majors take the same path as any release.** Patch
   releases replace the served patch; a moved base-image digest is a rebuild; a new
   major is just a new minor to rotate in. There is no separate manual lane.
5. **Alert on what needs a human, not on success noise.** Failures, rollbacks, rejected
   bumps and repeated check failures message through `shock-relay`; one alert per
   distinct failed plan.
6. **Cheap to check, expensive only when something changed.** The checks run daily;
   they rebuild or restart nothing unless a version, patch or digest moved.
7. **Elevated steps are handed over.** Agents cannot run `sudo`: tooling prepares
   everything and hands the exact root command to the user (see the Sudo Boundary).

## Adopting this in a repository

```yaml
jobs:
  install-check:
    uses: casonk/.github/.github/workflows/install-check.yml@main
    with:
      python-versions: auto-edges
  ci:
    uses: casonk/.github/.github/workflows/python-ci.yml@main
    with:
      python-versions: auto
```

Check where a repo stands with
`python3 scripts/check_ci_python_versions.py --repo ../<repo>`, or the whole workspace
with `--root /path/to/git`.

## Known costs

- Check names change when the matrix shifts (`ci / test (3.13)` becomes
  `ci / test (3.16)`), so a branch-protection rule that requires a specific matrix
  check by name must require the resolver job instead, or use a ruleset on the
  workflow, not the leg.
- Codecov flags are per Python version (`python-3.15`), so the flag changes when the
  window moves.
- Playwright 1.65+'s bundled driver needs `libatomic1`; `python:*-slim` images do not
  ship it.
