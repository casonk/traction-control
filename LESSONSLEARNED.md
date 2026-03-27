# LESSONSLEARNED.md — Portfolio Control Plane

> Purpose: record durable lessons that should change how future agents work across the portfolio.
> Unlike `CHATHISTORY.md`, this file is tracked and should keep only reusable lessons.

## How To Use This File

- Read this file before repeating setup, publishing, audit, or automation workflows.
- Add lessons that generalize beyond a single session.
- Keep entries concise and action-oriented.
- Do not use this file for transient status updates or full session logs.

## Lessons

### 2026-03-26 — GitHub CLI access is unreliable inside the sandbox

- `gh` commands that need GitHub API access may fail inside the sandbox even when local login appears to succeed.
- Verify actual API access before relying on `gh auth` output alone.
- Expect `gh repo create`, `gh repo view`, and similar networked commands to need escalated execution in this environment.
- Prefer SSH remotes for `git push` when an SSH key is already available.

### 2026-03-26 — Avoid committing absolute local filesystem paths

- Do not commit local absolute paths when relative paths or location-neutral wording communicate the same workflow.
- Prefer references like `./util-repos/traction-control`, `../..`, or "portfolio root" over machine-specific mount points.
- Treat committed local path disclosure as a documentation hygiene and security issue, not just a style preference.

### 2026-03-26 — Inventory CI by workflow presence, not only by `ci.yml`

- Some repositories satisfy the CI standard through differently named workflows such as `black-pylint-pytest.yml` or publish-specific pipelines paired with test workflows.
- When auditing CI coverage, inspect `.github/workflows/` broadly before concluding that a repository has no CI.
- Prefer reporting "no workflow files found" over "missing `ci.yml`" unless the exact filename is itself the requirement being audited.
