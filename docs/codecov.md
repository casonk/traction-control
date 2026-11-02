# Codecov

Codecov shows test coverage per repository and per pull request. This portfolio
uses it on a few repositories, from CI, with the upload credential kept in the
auto-pass vault. Nothing here needs a paid plan.

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
2. Install the Codecov GitHub app on the account and choose the repositories to
   try. A private repository must be granted explicitly.
3. Store the account login in the vault, under a path this repository is **not**
   allowed to read:

   ```bash
   auto-pass set web/codecov --url https://app.codecov.io --username YOUR_LOGIN --password-stdin
   ```

## Add a repository

Do this per repository, `NAME` being its name without the owner.

1. In Codecov, open the repository, then **Settings > General**, and copy the
   **Repository Upload Token**.
2. Store it in the vault. The entry path is fixed (`dev/codecov/NAME`); the token
   is typed without echo and never lands in shell history:

   ```bash
   read -rs -p 'Codecov upload token: ' CODECOV_TOKEN_VALUE; echo
   printf '%s' "$CODECOV_TOKEN_VALUE" | auto-pass set dev/codecov/NAME --password-stdin \
     --url https://app.codecov.io/gh/casonk/NAME
   unset CODECOV_TOKEN_VALUE
   ```

3. Copy it to the repository's `CODECOV_TOKEN` Actions secret:

   ```bash
   scripts/codecov_secrets.py status --repo NAME        # vault entry and secret state
   scripts/codecov_secrets.py push --repo NAME --dry-run
   scripts/codecov_secrets.py push --repo NAME
   ```

   `push` reads the entry through the auto-pass provisioning daemon (it must be
   running and unlocked: `auto-pass daemon-status`), sets the secret over the
   standard input of `gh secret set`, and prints nothing secret. `--only-missing`
   leaves a secret that is already set. The allowlist entry that lets this
   repository read `dev/codecov/*` lives in auto-pass's ignored
   `config/provisioning-allowlist.local.toml`.

4. Turn coverage on in the repository's `.github/workflows/ci.yml`:

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

## Without a stored token (OIDC)

Codecov can verify the workflow's own GitHub identity instead of a token, so there
is nothing to store or rotate:

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
would rather not keep a token; the token route above is the one that uses the vault.

## Reading the result

- The upload step is quiet on success; the report appears on the repository's page in
  Codecov and as a pull-request comment once the app is installed.
- `fail_ci_if_error` is off (`coverage-fail-on-error: false`), so a Codecov outage
  warns and does not fail CI. Turn it on for a repository once uploads are reliable.
- Start with Codecov's default status checks informational; add a `codecov.yml`
  target only once the baseline number is known.

## Rotate or remove

- **Rotate:** regenerate the token in Codecov, repeat the `auto-pass set` command,
  then `scripts/codecov_secrets.py push --repo NAME`.
- **Remove:** delete the repository's token in Codecov, `gh secret delete
  CODECOV_TOKEN -R casonk/NAME`, remove the vault entry, and drop `coverage: true`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `status` shows `vault entry: denied` | the auto-pass allowlist does not list `dev/codecov/*` for this repository, or the daemon was not reloaded after the edit |
| `vault entry: locked` | run `auto-pass unlock` |
| `vault entry: missing` | the entry path must be exactly `dev/codecov/NAME` |
| CI says "Coverage was measured but not uploaded" | no `CODECOV_TOKEN` secret and `coverage-use-oidc` is false |
| Upload fails with "token required" | the secret is empty or belongs to a different repository |
| Private repository shows nothing | the Codecov app has not been granted that repository |
