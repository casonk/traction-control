# Security Policy

## Reporting

Do not file sensitive disclosures in public issues.

Report security issues privately to the repository owner or maintainer instead
of publishing exploit details in a public issue or pull request.

## Scope

This repository must not become a place to store live secrets, credentials,
tokens, private keys, personal data, or other private environment details.

- Treat `CHATHISTORY.md` as local-only operational memory and do not publish
  it.
- Do not commit machine-specific absolute filesystem paths, hostnames, internal
  endpoint addresses, wallet identifiers, or local-only config files unless the
  exact value is strictly required and already safe to disclose.
- Treat tracked example files, fixtures, screenshots, copied logs, and issue or
  pull-request snippets as public documentation. Use synthetic placeholders and
  redacted examples instead of real usernames, hostnames, account identifiers,
  secrets, or private operational data.
- Prefer `localhost` or loopback-only defaults for local dashboards and admin
  utilities unless a wider network bind is explicitly required by the design.
- If the repo exposes a dashboard or admin surface with state-changing actions,
  do not rely on deployment docs alone to provide the trust boundary. Keep the
  safe default on loopback unless explicit authentication or wider network
  exposure is intentionally designed in.
- Treat screenshots, logs, sample configs, and copied terminal output as
  potentially sensitive if they reveal credentials, tokens, certificates, or
  private operational topology.

## Safe Documentation Practices

- Use generic paths, placeholder usernames, and redacted examples in tracked
  docs unless a concrete value is required for the workflow.
- Keep durable security guidance in tracked files such as `SECURITY.md`,
  `AGENTS.md`, and `LESSONSLEARNED.md`.
- Keep transient local operational details in gitignored files such as
  `CHATHISTORY.md` or local env/config files.
