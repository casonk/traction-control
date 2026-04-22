You are running an unattended daily bug-sweep task inside `./util-repos/traction-control`.

Goal:
- Review the clean code-focused repos listed below for potential bugs, regressions, or missing validation.
- Produce a concise findings-first review summary with file references.

Working rules:
- Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` in `traction-control` first.
- Work only on repos listed as `candidate` in the appended inventory.
- Read each target repo's `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` before review.
- Treat this as a code-review pass. Findings should be the primary output.
- Prioritize concrete bugs, behavioural regressions, missing guards, and missing tests over style issues.
- Prefer a read-only review. Do not modify target repos unless a very small, high-confidence, locally verifiable fix is clearly warranted.
- If you do edit a target repo, keep the change minimal, run the repo-local verification gate from `AGENTS.md`, and update local `CHATHISTORY.md` in that repo plus `traction-control`.
- If you do not edit a repo, leave tracked files untouched and report findings only through the task output.
- Skip any repo that becomes dirty while you are working and mention the skip in the summary.
- Do not rewrite history or revert unrelated user changes.

Verification:
- For every repo you modify, run its local CI gate from `AGENTS.md`.
- If you update only `traction-control/CHATHISTORY.md` or `LESSONSLEARNED.md`, run file-scoped `pre-commit`.

Outcome:
- Keep the final summary concise and findings-first.
- Proceed autonomously and do not wait for user input.
