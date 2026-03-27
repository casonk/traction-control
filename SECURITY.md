# Security Policy

## Reporting

Do not file sensitive disclosures in public issues.

For this repository, security-sensitive reports should be handled privately by the repository owner because this repo contains cross-repository governance context and may reference portfolio structure.

When reporting an issue, redact any local environment details that are not required to understand the problem.

## Scope

- Do not include credentials, local-only tokens, or personal data in issues or pull requests.
- Do not commit machine-specific absolute filesystem paths, mount points, usernames, hostnames, or other unnecessary local-environment identifiers.
- Prefer relative paths and location-neutral wording in committed documentation unless an absolute path is strictly required for the task.
- Treat `CHATHISTORY.md` as local-only operational memory.
- Treat local auth state, device codes, and session-specific login output as sensitive operational context unless they are clearly safe to disclose.

## Safe Documentation Practices

- Use relative references such as `./util-repos/traction-control` and `../..` in committed docs when they are sufficient for repo operations.
- Keep durable operational guidance in tracked docs such as `AGENTS.md` and `LESSONSLEARNED.md`.
- Keep transient workflow notes in local-only `CHATHISTORY.md`.
