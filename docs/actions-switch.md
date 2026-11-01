# GitHub Actions Switch

`scripts/actions_switch.py` turns GitHub Actions on or off for every portfolio
repository and tells the owner when it has been left off. It exists because
Actions is paused when the monthly minutes run out and has to come back when the
month turns over, and because GitHub switches off scheduled workflows on its own.

## Two layers

Actions has two independent switches, and re-enabling one does not touch the
other:

- **Repository level** (`actions/permissions`): `enabled` true or false. This is
  what a pause changes.
- **Workflow level**: each workflow is `active`, `disabled_manually`, or
  `disabled_inactivity`. GitHub sets `disabled_inactivity` itself after 60 days
  with no repository activity, and a repository-level re-enable leaves it set.
  A workflow in that state does not run on push either.

Archived repositories are read-only on GitHub, so every command reports and
skips them.

## Commands

The repository list comes from the ignored visibility registry
(`config/repository-visibility/{private,public}.local.json`, plus
`local-private.local.json` when present), so there is no second list to drift.
`--owner NAME` lists that account's repositories instead. `--repo` (repeatable;
slug or bare name) narrows any command.

```bash
scripts/actions_switch.py status                   # repository and workflow state
scripts/actions_switch.py disable --yes            # pause everything, recording why
scripts/actions_switch.py disable --repo NAME      # pause one repository
scripts/actions_switch.py enable --dry-run         # preview
scripts/actions_switch.py enable                   # switch on, fix stuck workflows
scripts/actions_switch.py usage                    # this month's Actions minutes
scripts/actions_switch.py check                    # what needs attention (exit 1 if any)
```

- `enable` also re-enables `disabled_inactivity` workflows. `--include-manual`
  adds `disabled_manually` ones, which someone may have switched off on purpose.
- `disable` without `--repo` needs `--yes` (or `--dry-run`). It records the time
  and `--reason` (default "minutes exhausted") in the local state file.
- A failure on one repository is reported and does not stop the rest; the exit
  status is 1 if any failed.

## The check

`check` never changes GitHub. It reports:

| Finding | Meaning |
|---|---|
| off since a previous month | a pause you recorded is still in force after the month rolled over |
| off with no recorded reason | Actions was switched off, but not by `disable` |
| workflow `disabled_inactivity` | GitHub switched a workflow off; `enable` fixes it |
| private-repository minutes at or above `--warn-percent` (default 80) of the limit | the month's allowance is nearly spent |
| a charge on the Actions bill | minutes beyond the allowance are costing money |

A pause you recorded this month is counted but not reported, so a deliberate
pause does not nag. With `--notify` it sends one message to Signal, Telegram and
Gmail through shock-relay and repeats a finding only after `--renotify-hours`
(default 24); a finding that clears is forgotten, so a recurrence announces
again. State is saved only when a channel delivered, so a failed send is retried.

Targets are `ACTIONS_SWITCH_NOTIFY_{SIGNAL,TELEGRAM,GMAIL}_TARGET` (optional
`..._CONFIG`) in `~/.config/traction-control/actions-switch.env`; a channel
without a target is reported and skipped. `JOB_GUARD_SHOCK_RELAY_ROOT`
overrides the shock-relay checkout, as for the job guard.

Install the daily timer from `config/clockwork/actions-switch-check.toml.template`:
substitute `__REPO_ROOT__`, run `python3 -m clockwork install --manifest FILE
--target systemd-user --unit-dir ~/.config/systemd/user` from the sibling
clockwork checkout, then `systemctl --user enable --now
actions-switch-check.timer`.

## Billing usage

`usage` and the minutes check read GitHub's billing usage report, which needs
the `user` scope on the token. Without it GitHub answers 404 and the tool says
how to add it (`gh auth refresh -h github.com -s user`); `check` then carries on
without the minutes findings, and `--no-usage` skips the lookup.

What the report contains decides how it is read:

- **Only private repositories use the allowance.** Public-repository minutes
  appear at full price and are then discounted to nothing, so they are counted
  apart and never warn. A repository the registry cannot classify counts as
  private, which errs toward warning. With `--owner` the repository listing's
  `private` flag does the same job.
- **Minutes are weighted.** A Windows minute uses two allowance minutes and a
  macOS minute ten; the multiple is the item's price over the Linux price.
- **Storage and other products are ignored.**
- **A real charge is its own finding** (`netAmount` above zero), whatever the
  percentage.

The limit comes from your GitHub plan (free 2000, pro and team 3000), else 2000;
`--limit-minutes` or `ACTIONS_SWITCH_LIMIT_MINUTES` overrides it.

October 2026 read 2013 weighted private minutes against 2000, with 11335 public
minutes free and nothing billed, which is why Actions was paused.

## State

`~/.local/state/portfolio-actions-switch/state.json` (directory 0700, file 0600)
holds the recorded pauses and the last notification time per finding. It names
repositories, so keep it out of backups that leave the machine.
