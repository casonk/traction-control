# Job Guard

`scripts/job_guard.py` keeps scheduled jobs from starting on a machine that is
already busy or hot, runs heavy jobs one at a time, and gives every scheduled
job a time limit.

## Why

Each scheduled job was written as if it ran alone. On one workstation, several
of them firing in the same window ran the processor to its thermal limit for
hours, and one job with no time limit never finished. No job was wrong; nothing
was looking at the machine.

## How it works

The guard attaches to systemd units, not to the jobs:

- `install` writes a drop-in (`50-portfolio-job-guard.conf`) for every service
  that a timer in the user unit directory activates. Nothing in the unit files,
  the clockwork manifests that render them, or the jobs changes.
- The drop-in adds `ExecCondition=... job_guard.py check --unit %n` and, when
  the unit declares no limit of its own, a time limit: `TimeoutStartSec=` for a
  oneshot unit, `RuntimeMaxSec=` for any other type.
- `check` exits 0 to let the job start or 75 to defer it. systemd records a
  deferred start as skipped, not failed.
- A deferred job is retried through a transient timer after `retry_minutes`
  plus random jitter. After `max_deferrals` the trigger is dropped and the
  unit's own timer fires again on schedule.

## Job classes

| Class | Waits when | Runs alongside other jobs |
|---|---|---|
| `heavy` (default) | CPU or GPU is hot, load is high, or an earlier heavy job is running | no, one at a time |
| `light` | CPU is at the critical temperature | yes |
| `exempt` | never; gets a time limit only if configured | yes |

When two heavy jobs are released together, the one that started first (then
the lower name) proceeds and the other defers, so they cannot defer to each
other forever.

## Failing open

The guard never turns its own problems into stopped jobs. A sensor that cannot
be read is ignored, and a broken config or an unexpected error lets the job
start and logs the reason. The cost is that a misconfigured guard protects
nothing; `status` shows what it currently sees.

## Use

```bash
python3 scripts/job_guard.py install --dry-run   # what would be written
python3 scripts/job_guard.py install             # write drop-ins and reload
python3 scripts/job_guard.py status              # readings and per-job verdict
python3 scripts/job_guard.py install --remove    # take the drop-ins out again
```

Re-run `install` after adding or removing scheduled units. A unit rendered by
clockwork with `start_condition` and `time_limit` already carries the check and
its limit; `install` leaves those alone (`own check kept`, `own limit kept`) and
still records the unit's class.

Policy is optional and local. Copy
[`config/job-guard/config.example.toml`](../config/job-guard/config.example.toml)
to `~/.config/portfolio-job-guard/config.toml` to change thresholds or to mark
a unit `light` or `exempt`. Deferral counts live under
`~/.local/state/portfolio-job-guard/`.

## Notifications

`check` appends an event to `~/.local/state/portfolio-job-guard/events.jsonl`
when it drops a job after `max_deferrals`; the guard never sends anything
itself. `scripts/job_guard_notify.py` reads those events and the per-unit
deferral state and sends at most one message per run, to Signal, Telegram and
Gmail through shock-relay (`--channels` narrows it):

- **dropped**: every job dropped since the last message, listed together;
- **pressure**: every job that has been deferring for `--pressure-minutes`
  (default 120) or more, repeated after `--renotify-hours` (default 6) while it
  lasts. When it clears it is forgotten, so a new episode notifies at once.

One hot evening is one message, not one per job. Targets are
`JOB_GUARD_NOTIFY_{SIGNAL,TELEGRAM,GMAIL}_TARGET` (optional `..._CONFIG`) in
`~/.config/traction-control/job-guard-notify.env`; a channel without a target is
reported and skipped, and state is committed only when a channel delivered, so a
failed send is retried on the next run. `JOB_GUARD_SHOCK_RELAY_ROOT` overrides
the shock-relay checkout. Preview with `job_guard_notify.py --dry-run`. The
notifier's own unit is never guarded (`install` skips it), so it still runs
when every other job is deferred. Install the timer from
`config/clockwork/job-guard-notify.toml.template` (every 15 minutes): substitute
`__REPO_ROOT__`, run `python3 -m clockwork install --manifest FILE --target
systemd-user --unit-dir ~/.config/systemd/user` from the sibling clockwork
checkout, then `systemctl --user enable --now job-guard-notify.timer`. The Telegram
route stays skipped until its bot token exists.

## Limits

- Linux with a systemd user instance only. launchd has no equivalent of
  `ExecCondition`, so macOS jobs are not covered.
- GPU temperature is read through `nvidia-smi` when present.
- "Another heavy job is running" is a check, not a lock: two jobs released in
  the same instant are ordered deterministically, but a job started by hand is
  not seen as a peer unless it is one of the guarded units.
