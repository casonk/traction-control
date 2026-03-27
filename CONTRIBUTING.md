# Contributing

`traction-control` is the portfolio-governance repo for `/mnt/4tb-m2/git`.

## Workflow

1. Make cross-repo guidance changes here first.
2. When auditing the portfolio, scan from `/mnt/4tb-m2/git`, not from this repo root.
3. Keep portfolio-wide instructions factual, current, and lightweight enough to stay maintainable.
4. Use Conventional Commits such as `docs: update portfolio guidance` or `chore: add repo baseline`.

## Content Standards

- `AGENTS.md` should describe portfolio behavior from the perspective of the portfolio root.
- `CHATHISTORY.md` is local-only and should stay concise.
- Do not embed credentials, local machine secrets, or personal data in control-plane docs.
- When the repo inventory changes, update both the repo landscape and any summary guidance that depends on it.

## Pull Requests

- Keep each pull request focused on one governance theme.
- Call out cross-repo implications explicitly.
- Note whether any inventory or baseline counts were refreshed.
