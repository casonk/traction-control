You are running an unattended maintenance task inside `./util-repos/traction-control`.

Goal:
- Review every repo-level `SECURITY.md` and `LESSONSLEARNED.md` under the portfolio root.
- Promote repeated, broadly applicable guidance into the shared templates in this repo.

Working rules:
- Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` in `traction-control` first.
- Scan from the portfolio root, not only from `util-repos/`.
- Treat this as a template-consolidation pass, not a repo-wide rewrite.
- Promote guidance only when it is repeated or clearly applicable across multiple repos.
- Prefer editing only:
  - `docs/templates/SECURITY.md`
  - `docs/templates/LESSONSLEARNED.md`
  - `LESSONSLEARNED.md` in `traction-control`
  - `CHATHISTORY.md` in `traction-control`
- Only touch repo-level `SECURITY.md` or `LESSONSLEARNED.md` elsewhere if a clear shared-pattern follow-up is warranted and the target tracked files are clean.
- Do not rewrite unrelated files or revert existing user changes.
- If no meaningful shared-template promotion is warranted, leave tracked files unchanged except for a concise `CHATHISTORY.md` note documenting the scan outcome.

Verification:
- If `docs/templates/SECURITY.md`, `docs/templates/LESSONSLEARNED.md`, or `LESSONSLEARNED.md` changed, run:
  - `pre-commit run --files docs/templates/SECURITY.md docs/templates/LESSONSLEARNED.md LESSONSLEARNED.md`
- If `docs/templates/SECURITY.md` changed, run:
  - `python3 scripts/check_security_md.py --repo "$(git rev-parse --show-toplevel)"`
- If you edit repo-level `SECURITY.md` or `LESSONSLEARNED.md` elsewhere, run file-scoped verification in those repos too.

Outcome:
- Keep the final summary concise.
- Proceed autonomously and do not wait for user input.
