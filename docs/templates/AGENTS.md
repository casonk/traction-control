# AGENTS.md — `<repo-name>`

> Seed template. Replace `<repo-name>` and the bracketed guidance, keep the
> section set. `scripts/check_agents_md.py` verifies the shared conventions
> below; it is concept-based, so rewording a section is fine as long as the
> rule survives.

## Purpose

[One paragraph: what this repository is for, and what it is deliberately not
for. State the real execution or integration flow, not the folder list.]

## Repository Layout

[The directories that matter and what lives in each. Call out any local-only,
generated, or gitignored boundaries explicitly so published behavior is not
confused with offline material.]

## Setup And Commands

```bash
# Install
[install command]

# Tests
[test command]

# Pre-commit
pre-commit run --all-files
```

## Operating Rules

[Repo-specific constraints. Keep tracked examples, fixtures, and `.example`
templates scrubbed of real paths, usernames, hostnames, and account
identifiers — real operator data belongs only in gitignored local config.]

## Sudo Boundary

Agents cannot run `sudo` in this environment. Complete all non-elevated repo
work and run the validation that does not need elevation, then hand the exact
elevated command(s) to the user to run themselves rather than retrying `sudo`.

Do not claim a sudo-backed live change was applied until the user shares the
result.

## Local CI Verification

[Required once the repo ships a CI workflow. Give the exact commands that
reproduce CI locally, in order.]

```bash
pre-commit run --all-files
[test command]
```

Auto-fixing hooks (`ruff`, `ruff-format`, `trailing-whitespace`,
`end-of-file-fixer`) rewrite files and exit 1 on the run that made the change.
Re-run, confirm exit 0, then stage what the formatter rewrote. Keep
`.pre-commit-config.yaml` revs in sync with the versions CI pins; a stale local
pin passes on rules the newer CI version rejects.

## Agent Memory

- `CHATHISTORY.md` — local-only session memory. Gitignored, never published.
- `LESSONSLEARNED.md` — tracked durable lessons that should change how future
  sessions work in this repo.
- `REFS-LOCAL.md` — gitignored machine-specific reference notes.
- `REFS-PUBLIC.md` — tracked public references.

Before final reporting for meaningful work, either add any durable lesson
discovered during the request or explicitly state why no durable lesson was
added. If the lesson generalizes beyond this repo, add it to
`traction-control/LESSONSLEARNED.md` instead — a lesson written into two
sibling repos belongs in the control plane.

## Portfolio Standards Reference

Portfolio-wide standards, the repository baseline, and the reusable-workflow
strategy live in the control plane at `./util-repos/traction-control`:

- `AGENTS.md` — portfolio baseline, tiers, and agent operating rules
- `LESSONSLEARNED.md` — durable cross-repo lessons
- `SECURITY.md` — portfolio security policy

Name only public shared utilities here. Private shared utilities and their
paths belong in gitignored local guidance derived from the visibility
registry.
