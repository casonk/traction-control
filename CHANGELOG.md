# Changelog

All notable changes to `traction-control` are documented here.

## Unreleased

- Initialized `traction-control` as the portfolio control-plane repository.
- Migrated the portfolio-wide `AGENTS.md` and `CHATHISTORY.md` from the former workspace root into this repo.
- Rolled out the portfolio baseline files across the other repositories, including governance docs, architecture blueprints, and repo-appropriate CI where needed.
- Added a local CI workflow for `traction-control` so the control-plane repo validates its own baseline.
- Standardized `LESSONSLEARNED.md` as a tracked convention across the portfolio repositories.
- Added a control-plane rule to check post-push workflow results and treat new CI failures as part of the same rollout.
- Standardized a repo-level `AGENTS.md` reference back to `traction-control` so agents can find the shared portfolio conventions from any repo.
- Documented `auto-pass`, `nordility`, and `shock-relay` as shared portfolio utilities and propagated that guidance into repo-level `AGENTS.md` files.
- Added `archility` as the shared architecture inventory utility repo and updated the control-plane/shared-utility guidance to advertise it portfolio-wide.
