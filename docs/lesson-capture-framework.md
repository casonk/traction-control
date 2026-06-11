# Lesson Capture Framework

This framework prevents reusable operational lessons from being left only in
conversation or `CHATHISTORY.md`.

## Capture Gate

Run this gate before the final response for every meaningful session:

1. Identify what changed.
2. Ask whether the work revealed a reusable rule, failure mode, workflow
   pattern, provider quirk, verification requirement, or safety boundary.
3. If yes, update the target repo's `LESSONSLEARNED.md`; for cross-repo or
   portfolio-wide behavior, also update `traction-control/LESSONSLEARNED.md`.
4. If no, say in the final response that no durable lesson was added because
   the outcome was session-specific.
5. Report which `CHATHISTORY.md` and `LESSONSLEARNED.md` files were updated.

## What Belongs In Lessons

Add a durable lesson when it should change future behavior, including:

- a repeated mistake or missed required step
- a safe implementation pattern that avoids regressions
- a tool, provider, systemd, CI, browser, API, or storage quirk
- a security, privacy, sudo, credential, or local-only data boundary
- a test or validation condition that future work should remember

Do not add a durable lesson for:

- transient status
- one-off command output
- work summaries with no future operating rule
- details that belong only in local `CHATHISTORY.md`

## Final-Response Contract

For meaningful work, the final response should include one of:

- `Lesson added: <path>`
- `No durable lesson added: <reason>`

This makes skipped lesson capture visible immediately instead of requiring the
user to ask after the fact.
