# REFS-PUBLIC.md - Public References

> Record external public repositories, datasets, documentation, APIs, or other
> public resources that this repository utilizes or depends on.
> This file is tracked and intentionally kept free of private or local-only details.

## Public Repositories

- No fixed external code repository is the main upstream; this repo documents internal portfolio conventions and scheduled governance flows.

## Public Datasets and APIs

- No standing public data APIs are required; the control plane primarily inspects the local portfolio workspace.

## Documentation and Specifications

- https://docs.github.com/en/actions - GitHub Actions reference for the control-plane CI workflow
- https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work - Git commit and tag signing concepts used by the portfolio signing standard
- https://docs.github.com/en/authentication/managing-commit-signature-verification - GitHub hosted signature-verification behavior
- https://cli.github.com/manual/gh_gpg-key - GitHub CLI public GPG-key registration commands
- https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html - user timer semantics for the scheduled audit and install scripts

## Notes

- Sibling utility repos such as archility, clockwork, and tachometer are internal portfolio references and therefore stay out of this public-only file.
