You are running an unattended GitHub Actions CI-repair task inside `./util-repos/traction-control`.

Goal:
- Review the current failing default-branch push CI runs listed below.
- Repair failing CI in the affected clean repos and get the hosted workflows green again.

Working rules:
- Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` in `traction-control` first.
- Work only on repos listed as `candidate` in the appended inventory.
- Read each target repo's `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` before edits.
- Treat this as a CI-repair pass, not a general repo sweep.
- Before editing a target repo, confirm its worktree is still clean. If it is dirty, skip it and document why.
- Inspect the failing run(s) with `gh` or equivalent GitHub tooling before changing code.
- Prefer the smallest fix that addresses the actual hosted CI failure.
- Run the repo-local verification gate from `AGENTS.md` before any push.
- If you commit or push, use Conventional Commits.
- After pushing a fix, check the resulting hosted workflow run(s) and continue until the repo is green or clearly blocked.
- Do not rewrite history or revert unrelated user changes.
- Update local `CHATHISTORY.md` in `traction-control` and in each repo you changed.
- Add a durable lesson to repo-local or control-plane `LESSONSLEARNED.md` when new guidance generalizes.

Verification:
- For every repo you modify, run its local CI gate from `AGENTS.md`.
- If you update only `traction-control/CHATHISTORY.md` or `LESSONSLEARNED.md`, run file-scoped `pre-commit`.

Outcome:
- Keep the final summary concise.
- Proceed autonomously and do not wait for user input.
