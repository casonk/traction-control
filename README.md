# traction-control

Portfolio control-plane repository for cross-repository agent guidance, maintenance standards, and session continuity.

This repo lives at:

- `/mnt/4tb-m2/git/util-repos/traction-control`

Its effective scan target is the portfolio root two levels up:

- `/mnt/4tb-m2/git`
- relative path from this repo: `../..`

## Purpose

- Store the portfolio-wide `AGENTS.md` used for cross-repo maintenance work.
- Store the portfolio-wide `CHATHISTORY.md` used for local session continuity.
- Define the baseline conventions for repositories under `/mnt/4tb-m2/git`.
- Act as the home repo for future cross-repo automation or inventory tooling.

## Working Rule

When auditing or maintaining the portfolio, scan from the portfolio root, not from the `traction-control` repo root.

Example:

```bash
PORTFOLIO_ROOT="$(cd ../.. && pwd)"
find "$PORTFOLIO_ROOT" -maxdepth 4 -type d -name .git | sort
```

## Key Files

- `AGENTS.md`: portfolio-wide agent instructions
- `CHATHISTORY.md`: local-only portfolio-wide session log
- `CONTRIBUTING.md`: contribution guidelines for this control-plane repo
- `CHANGELOG.md`: notable changes to the portfolio-governance layer

## Contributing

See `CONTRIBUTING.md`.

## License

MIT
