You are running an unattended weekly REFS audit task inside `./util-repos/traction-control`.

Goal:
- For each repo in the candidate inventory, check whether its `REFS-PUBLIC.md` accurately
  reflects the external resources the repo actually depends on or references.
- Identify gaps (missing references) and staleness (entries for things no longer used).
- Update `REFS-PUBLIC.md` when the gap is unambiguous and the correct entry is clear.
- Report findings across all repos in a concise summary.

Working rules:
- Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` in `traction-control` first.
- Work only on repos listed as `candidate` in the appended inventory (`has_refs` or `missing_refs` status).
- For each target repo, read its `AGENTS.md` and `LESSONSLEARNED.md` before reviewing.
- To audit a repo's REFS-PUBLIC.md, read the following for external-reference signals:
    - `requirements.txt`, `pyproject.toml`, `setup.py`, `package.json` — package deps that imply upstream APIs
    - `config/*.toml`, `config/*.yaml`, `config/*.json` — declared series IDs, endpoint URLs, data sources
    - `*.py`, `*.sh`, `*.ts` — hardcoded URLs, API base constants, `requests.get(...)` calls, `import` statements
    - `REFS-PUBLIC.md` itself — compare what is documented against what you find above
- For repos with `missing_refs` status: determine whether `REFS-PUBLIC.md` is warranted. If yes,
  create a minimal, accurate file covering the actual external dependencies found.
- For repos with `has_refs` status: update the file if gaps or stale entries are evident and
  the correct content is clear. Do not speculate about resources not visible in the code.
- Do not invent entries. Only add references that are traceable to actual code or config.
- Preserve existing structure and formatting conventions in `REFS-PUBLIC.md` files.
- After editing a repo, run its local CI gate from `AGENTS.md` (typically `pre-commit run --all-files`).
- If the CI gate passes, stage and commit the changes with a concise commit message.
- Update `traction-control/CHATHISTORY.md` with a one-line entry per repo modified.

Verification:
- For every repo you modify, run its local CI gate.
- If you update only `traction-control/CHATHISTORY.md` or `LESSONSLEARNED.md`, run file-scoped `pre-commit`.

Outcome:
- Keep the final summary concise and findings-first: list repos audited, changes made, gaps found but not fixed.
- Proceed autonomously and do not wait for user input.
