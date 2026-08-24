# Portfolio Convention Audit — 2026-08-23

Portfolio-wide scan of `AGENTS.md`, `SECURITY.md`, and `LESSONSLEARNED.md`
across all 25 repositories, with the reusable findings up-integrated into this
control plane and the per-repo remainder filed to each repo's `BACKLOG.md`.

Reproduce with:

```bash
PORTFOLIO_ROOT=~/dev bash scripts/portfolio-audit.sh
python3 scripts/find_duplicate_lessons.py --portfolio-root ~/dev
```

## Scope Correction

The portfolio is **25 repositories, not 24**. `install-harness` lives at
`~/dev/install-harness` rather than under `util-repos/` or `sec-repos/`, so
sweeps that iterate those two directories miss it entirely. The daily audit
finds it because it discovers repos by `.git` directory under
`PORTFOLIO_ROOT`. Prefer that discovery method over directory enumeration.

## What Changed In The Control Plane

### Governance lint is now concept-based for `AGENTS.md`

`portfolio-audit.sh` verified the sudo boundary with a literal `grep -qF` for
one repo's exact sentence. `glovebox`, `service-manual`, and `differential`
each carry a correct, complete Sudo Boundary section in different wording and
were reported as gaps on every run — three standing false positives sitting in
the same list as the two repos that genuinely had none.

`check_security_md.py` had already been written concept-based for exactly this
reason, but the reasoning lived in that script's docstring rather than in
`LESSONSLEARNED.md`, so it never transferred. It has now been generalized into
`scripts/check_agents_md.py`, which checks four shared conventions:

| Convention | Rule enforced |
| --- | --- |
| Sudo boundary | Denies `sudo` to the agent **and** hands the exact command to the user — any wording. A heading alone, or a bare mention of `sudo`, does not pass. |
| Portfolio standards backlink | A standards/references heading that points at the control plane. |
| Session-memory boundary | Names both `CHATHISTORY.md` (local-only) and `LESSONSLEARNED.md` (durable). |
| Local CI verification | Required **only** where the repo ships a workflow, so docs-only repos are not held to it. |

### `check_security_md.py` accepts boundary headings already in use

`differential` documents its boundary under `## Trust Boundary` and
`## Safe Documentation`, which the accepted-heading regex did not list — a
fourth false positive. The pattern now also accepts `trust boundary`,
`repo-specific boundaries`, `delete safety`, and makes the trailing
`practices` optional on `safe documentation`.

### Ten lessons up-integrated

Portfolio-general rules that were stranded in a single repo, or duplicated
across two, now live in this repo's `LESSONSLEARNED.md`:

| Lesson | Source |
| --- | --- |
| Governance lint must be concept-based | this audit |
| A lesson duplicated in two repos belongs in the control plane | this audit |
| Merging to `main` in `casonk/.github` is a portfolio-wide deploy | `dot-github` |
| Suppressing stderr without validating the result hides hard failures | `auto-pass` |
| `stat -f` / `stat -c` fallbacks are not portability | `glovebox` |
| Secret scanners must be told ciphertext is not a leak | `glovebox` |
| Auto-fixing pre-commit hooks exit 1 on their first run | `crew-chief`, `dyno-lab` |
| Shell entrypoints need `shellcheck` in CI | `shock-relay` |
| AI session transcripts are secret-bearing; never use real ones as fixtures | `session-control` |
| Setup-script placeholder validation must reject realistic sample values | `short-circuit` + `snowbridge` (duplicated) |
| Caddy caches TLS certificates in memory at startup | `clockwork` + `wiring-harness` (duplicated) |

`scripts/find_duplicate_lessons.py` makes the duplication signal repeatable.
It found 8 cross-repo duplicates, all now resolved into the two entries above.

### `docs/templates/AGENTS.md` added

Tier 1 mandates `AGENTS.md` and specifies the sections it must carry, but the
plane shipped templates only for `BACKLOG.md`, `LESSONSLEARNED.md`,
`REFS-LOCAL.md`, `REFS-PUBLIC.md`, and `SECURITY.md`. New repos had nothing to
seed the mandated file from, which is the most likely reason the four outlier
repos drifted. The template carries all four checked conventions.

## Current Conformance

15 of 25 repositories pass every `AGENTS.md` convention check. 20 of 25 pass
the `SECURITY.md` policy check. Remaining gaps, filed to each repo's backlog:

### Outside the baseline entirely

| Repo | State |
| --- | --- |
| `install-harness` | 18 gaps. No `AGENTS.md`, `SECURITY.md`, `LESSONSLEARNED.md`, `BACKLOG.md`, `LICENSE`, or `.pre-commit-config.yaml`. |
| `drm-sec` | 17 gaps. Ships `.gitleaks.toml` and a baseline but no `secret-scan.yml` to run them — the config is dead. |
| `tor-mac` | 16 gaps. No security tooling of any kind. |
| `aterm-config` | 16 gaps. `BACKLOG.md` and `README.md` only; same dead-gitleaks-config problem as `drm-sec`. |

These four are credential- and network-adjacent — router configuration, DRM
research, Tor operation, install harness — which makes the missing
`SECURITY.md` boundaries more consequential than the file count suggests.

### Partially conformant

| Repo | Gaps |
| --- | --- |
| `auto-router-api` | 12. `AGENTS.md` references no portfolio convention at all. Has CI (green as of 2026-08-23) and runs gitleaks via pre-commit, but has no `secret-scan.yml`, so full history is never scanned. |
| `dot-github` | 8. No sudo boundary; no `BACKLOG.md`; no issue/PR templates. |
| `glovebox` | 2. No `CHATHISTORY.md` boundary; no Local CI Verification. |
| `service-manual` | 2. Same two. |
| `differential` | 1. No Local CI Verification. |
| `session-control` | 1. No `CHATHISTORY.md` boundary. |
| `windshield` | 1. No Local CI Verification. |

## Highest-Leverage Follow-Up

`casonk/.github` can supply default community health files — issue templates,
PR template, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md` — to every
repo in the account that lacks its own. Six repos are currently missing issue
and PR templates; adding them once to `dot-github` would close that column
portfolio-wide.

Verify the visibility interaction before relying on it: GitHub applies default
community health files from a public `.github` repo to public repos, and this
portfolio is private-first. Confirm the behavior for private repos rather than
assuming the defaults propagate.
