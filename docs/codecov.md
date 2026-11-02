# Codecov

Codecov shows test coverage per repository and per pull request. This portfolio
uses it on a few repositories, from CI. The one credential kept in the auto-pass
vault is a Codecov user API token (a PAT); each repository's upload token is
fetched from Codecov's API when needed and goes straight into that repository's
GitHub secret, so it is never typed, stored in a file or shown.

## What the free plan gives

Codecov's pricing page lists a free **Developer** plan for **one user**, with
unlimited uploads for public repositories and **250 uploads for private ones**.
The page's table is garbled, so the "per month" wording and whether private
repositories are included on the free plan are not confirmed: check the live page
before putting a private repository on it. Public repositories are the safe place
to start. A card is not mentioned as required.

## One-time setup (you do this)

These steps need your browser sign-in and your vault password, so they are not
scripted.

1. Sign in at <https://app.codecov.io> with **Sign in with GitHub** and authorise
   Codecov. Pick the free **Developer** plan.
2. Create a user API token (a personal access token) in your Codecov account
   settings and store it in the `infra` vault as `codecov/codecov-pat-user`
   (group `codecov`, entry `codecov-pat-user`, token in the password field).
3. Let this repository read exactly that entry. In auto-pass's ignored
   `config/provisioning-allowlist.local.toml`:

   ```toml
   [repos.traction-control.vaults.infra]
   allowed_paths = [
     "codecov/codecov-pat-user",
   ]
   ```

   Then tell the running daemon to reload, without restarting it (a restart locks the
   vault): send `{"op": "reload"}` on `~/.cache/auto-pass/provisioning.sock`.
   `auto-pass daemon-status` must show it running and unlocked.

## Add a repository

Do this per repository, `NAME` being its name without the owner.

1. Install the Codecov GitHub app on it (a private repository must be granted
   explicitly). Codecov must be able to see it: `status` below says so.
2. Copy its upload token into the repository's `CODECOV_TOKEN` Actions secret:

   ```bash
   scripts/codecov_secrets.py status --repo NAME
   scripts/codecov_secrets.py push --repo NAME --dry-run   # fetches, sets nothing
   scripts/codecov_secrets.py push --repo NAME
   ```

   `push` reads the PAT through the provisioning daemon, asks Codecov's API
   (`GET /repos/NAME/config/`) for the upload token, and sets the secret over the
   standard input of `gh secret set`. Neither token appears in an argument list or
   a message. `--only-missing` leaves a secret that is already set.

3. Turn coverage on in the repository's `.github/workflows/ci.yml`:

   ```yaml
   jobs:
     ci:
       uses: casonk/.github/.github/workflows/python-ci.yml@main
       with:
         python-versions: '["3.10", "3.12"]'
         coverage: true
         coverage-source: my_package      # or set [tool.coverage.run] source in pyproject.toml
       secrets:
         CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
   ```

   Coverage is measured on the **last** Python in the matrix only. Without the
   secret the coverage is still measured and the upload is skipped with a notice, so
   this can be merged before the token exists.

4. Start a run (`gh workflow run ci.yml`) and check `status`: the repository shows
   "has coverage" once Codecov has received it. Totals appear a minute or so later at
   `GET /repos/NAME/totals/`.

## Without a stored token (OIDC)

Codecov can verify the workflow's own GitHub identity instead of a token, so there
is nothing to copy or rotate:

```yaml
jobs:
  ci:
    permissions:
      contents: read
      id-token: write
    uses: casonk/.github/.github/workflows/python-ci.yml@main
    with:
      coverage: true
      coverage-use-oidc: true
```

The repository still has to be added in Codecov. Use this for a repository where you
would rather not keep an upload token in its GitHub secrets.

## Reading the result

- The upload step is quiet on success; the report appears on the repository's page in
  Codecov and as a pull-request comment once the app is installed.
- `fail_ci_if_error` is off (`coverage-fail-on-error: false`), so a Codecov outage
  warns and does not fail CI. Turn it on for a repository once uploads are reliable.
- `codecov.yml` in each trial repository makes every status check informational, so
  nothing blocks a pull request. Add a target there once the baseline is known.
- A test that asserts an exact timing can fail only under coverage, because tracing
  slows the code. Give such a test an injected clock.

## Rotate or remove

- **Rotate the PAT:** create a new one in Codecov, update the `infra` vault entry,
  revoke the old one. Nothing else changes.
- **Rotate a repository's upload token:** regenerate it in Codecov, then
  `scripts/codecov_secrets.py push --repo NAME`.
- **Remove a repository:** `gh secret delete CODECOV_TOKEN -R casonk/NAME`, drop
  `coverage: true`, and remove it in Codecov.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `PAT ...: denied` | the allowlist does not list `codecov/codecov-pat-user` in the `infra` vault for this repository, or the daemon was not reloaded after the edit |
| `PAT ...: locked` | run `auto-pass unlock` |
| `PAT ...: missing` | the entry must be exactly `codecov/codecov-pat-user` in the `infra` vault |
| `install the Codecov GitHub app on it` | Codecov cannot see the repository: grant the app access to it |
| `Codecov rejected the PAT` | the token expired or was revoked: create a new one |
| CI says "Coverage was measured but not uploaded" | no `CODECOV_TOKEN` secret and `coverage-use-oidc` is false |
| Upload fails with "token required" | the secret is empty or belongs to a different repository |
| Private repository shows nothing | the Codecov app has not been granted that repository |
