You are running an unattended Codecov coverage-repair task from `./util-repos/traction-control`.

Goal:
- Each row below is an open pull request where Codecov reported that the patch (the lines the pull request changes) is not fully covered by tests.
- Raise the patch coverage on the pull request's own branch by adding meaningful tests, so the pull request merges with its changed lines covered.

Working rules:
- Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` in `traction-control` first.
- Work only on rows whose `status` is `candidate`.
- Read the target repo's `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` before editing.
- Re-check each pull request first with `gh pr view <pr> -R <repo_slug> --json state,headRefName,isDraft`. If it is no longer open, skip it and say so.
- Read the Codecov comment (`comment_url`) and the files it names. The `files` column lists the files with missing lines and how many lines are missing.
- Do not edit the user's checkout of the repo. Create a git worktree of the pull request's head branch (`branch` column) outside the checkout, work there, and remove the worktree when finished. If the repo's checkout has uncommitted work, leave it alone.
- Write tests that exercise the changed behavior and its edge cases. Prefer the repo's existing test style, fixtures and helpers (`dyno-lab` where the repo uses it).
- Never raise coverage by adding `# pragma: no cover`, excluding files, lowering a coverage threshold, deleting code, or writing tests that assert nothing. Do not change non-test source unless a test exposes a real bug; if it does, fix the bug in its own commit and say so.
- Run the repo's full local verification gate from its `AGENTS.md` (at minimum `pre-commit run --all-files` and `pytest`) before pushing. Do not push if it fails.
- Commit with Conventional Commits (for example `test: cover the upscale staging paths`) and push to the pull request branch. Never push to the default branch and never open a second pull request for the same change.
- After pushing, check the new hosted run and the new Codecov comment. If patch coverage is still short, make one more focused pass; stop after two passes and report what is left.
- Hosted runners can be blocked by billing. If checks fail before any job starts, say so and do not treat it as a coverage problem.

Backlog:
- The monitor opens a separate pull request that adds a pending item to the repo's `BACKLOG.md`, tagged `[codecov:YYYY-MM-DD]` and containing `codecov-pr:<pr>`. Do not duplicate it.
- When you have raised the patch to fully covered, find that item (on the default branch or in its open backlog pull request) and mark it `[x]` with a one-line note in a small follow-up change on the default-branch flow (branch and pull request, never a direct push). If coverage is not fully raised, leave the item pending and add a short note of what remains.

Reporting:
- Update local `CHATHISTORY.md` in `traction-control` and in each repo you changed.
- Add a durable lesson to repo-local or control-plane `LESSONSLEARNED.md` only when it generalizes.
- Keep the final summary concise: one line per pull request with the coverage before and after, or why it was skipped.
- Proceed autonomously and do not wait for user input.
