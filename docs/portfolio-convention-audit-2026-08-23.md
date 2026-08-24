# Portfolio Convention Audit — 2026-08-23

Portfolio-wide scan of `AGENTS.md`, `SECURITY.md`, and `LESSONSLEARNED.md`
across every repository, with the reusable findings up-integrated into this
control plane and the per-repo remainder filed to each repository's
`BACKLOG.md`.

**Per-repository results are not recorded here.** This repository is public and
much of the portfolio is private, so naming which repositories carry which gaps
would enumerate private repository names and paths. The repo-by-repo detail
lives in the ignored local report at
`reports/portfolio-convention-audit-2026-08-23.md`; regenerate it any time with
the commands below.

Reproduce:

```bash
PORTFOLIO_ROOT="$(cd ../.. && pwd)" bash scripts/portfolio-audit.sh
python3 scripts/find_duplicate_lessons.py --portfolio-root "$(cd ../.. && pwd)"
```

## Discovery Correction

Repository discovery must walk `.git` directories from the portfolio root, not
enumerate the two well-known repo directories. At least one portfolio
repository is checked out directly under the portfolio root, so every sweep
built on directory enumeration silently omitted it — including the repository
count quoted in earlier reviews. `portfolio-audit.sh` already discovers by
`.git` and found it; ad hoc sweeps did not.

## What Changed In The Control Plane

### Governance lint is now concept-based for `AGENTS.md`

`portfolio-audit.sh` verified the sudo boundary with a literal `grep -qF` for
one repository's exact sentence. Three repositories each carry a correct,
complete Sudo Boundary section in different wording and were reported as gaps
on every run — three standing false positives sitting in the same list as the
two repositories that genuinely had none.

`check_security_md.py` had already been written concept-based for exactly this
reason, but the reasoning lived in that script's docstring rather than in
`LESSONSLEARNED.md`, so it never transferred. It has now been generalized into
`scripts/check_agents_md.py`, which checks four shared conventions:

| Convention | Rule enforced |
| --- | --- |
| Sudo boundary | Denies `sudo` to the agent **and** hands the exact command to the user — any wording. A heading alone, or a bare mention of `sudo`, does not pass. |
| Portfolio standards backlink | A standards/references heading that points at the control plane. |
| Session-memory boundary | Names both `CHATHISTORY.md` (local-only) and `LESSONSLEARNED.md` (durable). |
| Local CI verification | Required **only** where the repository ships a workflow, so docs-only repositories are not held to it. |

### `check_security_md.py` accepts boundary headings already in use

One repository documents its boundary under `## Trust Boundary` and
`## Safe Documentation`, which the accepted-heading regex did not list — a
fourth false positive. The pattern now also accepts `trust boundary`,
`repo-specific boundaries`, `delete safety`, and makes the trailing
`practices` optional on `safe documentation`.

### Ten lessons up-integrated

Portfolio-general rules that were stranded in a single repository, or
duplicated across two, now live in this repository's `LESSONSLEARNED.md`.
`scripts/find_duplicate_lessons.py` makes the duplication signal repeatable: it
found 8 cross-repo duplicates, all resolved into two entries.

Source repositories are named in the lesson entries only where the source is
public; private sources are described by capability instead.

### `docs/templates/AGENTS.md` added

Tier 1 mandates `AGENTS.md` and specifies the sections it must carry, but the
plane shipped templates only for `BACKLOG.md`, `LESSONSLEARNED.md`,
`REFS-LOCAL.md`, `REFS-PUBLIC.md`, and `SECURITY.md`. New repositories had
nothing to seed the mandated file from, which is the most likely reason the
outlier repositories drifted. The template carries all four checked
conventions.

## Conformance Summary

Counts only; see the ignored local report for which repository is which.

| Check | Passing |
| --- | --- |
| `AGENTS.md` shared conventions | 15 of 25 |
| `SECURITY.md` policy coverage | 21 of 25 |

Four repositories sit outside the baseline entirely, with no `AGENTS.md`,
`SECURITY.md`, or `LESSONSLEARNED.md`. They are credential- and
network-adjacent, which makes the missing security boundaries more
consequential than the raw file count suggests. Seven more are partially
conformant, most missing only a `Local CI Verification` section or the
`CHATHISTORY.md` boundary.

Two repositories ship a `.gitleaks.toml` and baseline with no workflow and no
pre-commit config to run them, so they read as scanned while nothing scans
them.

## Default Community Health Files

Repositories missing `.github/ISSUE_TEMPLATE/` and
`.github/PULL_REQUEST_TEMPLATE.md` can inherit them from the account-level
`.github` repository instead of each carrying its own copy.

The open question was whether inheritance survives the portfolio's
private-first posture, since the account `.github` repository is public.
**It does.** Verified empirically against a private repository that carries
neither file: `GET /repos/{owner}/{repo}/community/profile` resolves both
`code_of_conduct` and `contributing` to the public `.github` repository, while
`issue_template` and `pull_request_template` report absent — because the
`.github` repository does not yet supply them. Adding them there closes the gap
for every repository that lacks its own.

Because inheritance satisfies the requirement without a local file,
`portfolio-audit.sh` must treat these two entries as satisfied when the account
`.github` repository provides them. Otherwise the audit reports gaps that are
already closed, which is the same cry-wolf failure the concept-based rewrite
above was meant to end.
