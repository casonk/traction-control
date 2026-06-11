# Contributing

`traction-control` is the portfolio-governance repo for this workspace.

## Workflow

1. Make cross-repo guidance changes here first.
2. When auditing the portfolio, scan from `../..`, not from this repo root.
3. Keep portfolio-wide instructions factual, current, and lightweight enough to stay maintainable.
4. Use Conventional Commits such as `docs: update portfolio guidance` or `chore: add repo baseline`.

## Content Standards

- `AGENTS.md` should describe portfolio behavior from the perspective of the portfolio root.
- `LESSONSLEARNED.md` is the tracked durable-lessons file for portfolio-wide reusable guidance.
- `CHATHISTORY.md` is local-only and should stay concise.
- For meaningful work, run the lesson-capture gate in
  `docs/lesson-capture-framework.md` before final reporting.
- Do not embed credentials, local machine secrets, or personal data in control-plane docs.
- When the repo inventory changes, update both the repo landscape and any summary guidance that depends on it.

## Pull Requests

- Keep each pull request focused on one governance theme.
- Call out cross-repo implications explicitly.
- Note whether any inventory or baseline counts were refreshed.
