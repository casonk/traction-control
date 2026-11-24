# Resource Limits

Every scheduled job runs under explicit limits: a CPU quota, a memory ceiling,
and optionally how many other jobs may run beside it. The numbers come from what
the job was seen to use, and **raising one is a recorded decision**.
`scripts/resource_limits.py` derives them, records every change, and refuses a
raise that has no record.

## Why

A limit set once and edited whenever a job misbehaves stops meaning anything: the
quota that was meant to bound a job ends up wherever the last incident left it,
with no trace of why. Limits that are derived from evidence and raised only with
evidence stay meaningful, and a job that outgrows its ceiling shows up as a
decision someone made rather than a number that drifted.

## Where the data lives

| File | Tracked | Purpose |
|---|---|---|
| `config/resource-limits/limits.example.toml` | yes | Synthetic example of the registry. |
| `config/resource-limits/limits.local.toml` | no | The registry: one table per job. The arbiter reads it through its `limits_file` setting and enforces it. |
| `config/resource-limits/changes.local.jsonl` | no | The audit log: one line per change, each carrying the hash of the line before it. |

The registry and the log name real jobs, so they are ignored by Git like the
other `*.local.*` configs, and the local-state backup covers them
([local-state backup](local-state-backup.md)).

## Deriving limits

```bash
python3 scripts/resource_limits.py observe                # peaks per job from the journal
python3 scripts/resource_limits.py baseline --units-file JOBS.json          # what would be written
python3 scripts/resource_limits.py baseline --units-file JOBS.json --write  # record it as the approved baseline
```

`JOBS.json` maps each job name to its class (`heavy`, `light`, `exempt`); set
`RESOURCE_LIMITS_UNITS_FILE` to avoid passing it each time.

systemd logs `Consumed <cpu> CPU time, <mem> memory peak` when a unit stops.
From those lines:

- **memory** ceiling = the highest peak seen x 1.5, rounded up to 128 MiB, never
  below 512 MiB, and never below the class default (heavy 4096 MiB, light 1024 MiB)
  for a job seen fewer than 5 times. The job is killed above it, so the headroom is
  deliberate. A job whose real work runs in a separate scope reports a small peak
  for its own unit; the floor keeps that from becoming a trap.
- **CPU** quota = the busiest run's mean cores x 3, rounded up to 50%, never
  below the class floor (heavy 200%, light 100%) and never above 800%. Mean
  cores is CPU time over wall time, so it can only under-state a bursty job; the
  floor and the multiplier are what absorb that. A throttled job runs slower, it
  is not killed.
- A job with **no history** gets the class default and a note saying so; tighten
  it once it has run.

Observed history is production runs only. A job with side effects is not run
just to measure it.

## Raising a limit

```bash
python3 scripts/resource_limits.py raise some-job --memory-max 6144 \
    --reason "the monthly input doubled" --evidence "peak 4980 MiB on the 3rd, killed at 4608"
python3 scripts/resource_limits.py lower some-job --memory-max 2048
```

`raise` refuses to run without both `--reason` and `--evidence`, and writes the
audit entry before it touches the registry, so a failure between the two leaves
the registry at or below what is approved. Raising `cpu_quota`, `memory_max` or
`companions` (how many other jobs may run beside this one; higher is looser)
counts. Removing a limit counts as raising it. `lower` is free, is logged, and
cannot be used to raise. `nice` is a preference and is not regulated.

## Sizing worker pools

A limit only helps if the job fits inside it: a pool of 16 workers under a 200% quota is just
throttled. The scheduler therefore tells each job how big it may be, through two environment
variables set on every limited unit and lease:

| Variable | Meaning |
|---|---|
| `PORTFOLIO_CPU_CORES` | whole cores the job's CPU quota allows (rounded down, at least 1) |
| `PORTFOLIO_MEMORY_MIB` | the job's memory ceiling in MiB |

Tooling that starts parallel workers (process or thread pools, `-j` flags, container fan-out) reads
`PORTFOLIO_CPU_CORES` for its default worker count and falls back to its own default when the
variable is unset or not a positive integer; an explicit flag still wins. A tool must not name
the scheduler: it only reads the variable. Raising a job's quota (see above) therefore also raises
its parallelism at the next run, with no change to the tool.

I/O-bound pools (HTTP requests, API calls) do not need scaling. A benchmark that measures the whole
host should not be scaled either, or it would report the quota instead of the machine.

## Checking

```bash
python3 scripts/resource_limits.py check
```

The daily portfolio audit runs `check --no-journal` and fails when that exits 1.
Warning lines are logged and do not fail the audit. The journal scan stays off
that pass: reading 30 days of the user journal is slow, and the peaks are what
the weekly timer is for.

```bash
bash scripts/install_resource_limits_observe_systemd.sh
```

That installs `resource-limits-observe-weekly.timer` (Sunday 07:00, up to 30
minutes of jitter). It runs `scripts/resource_limits_weekly.sh`, which prints
`observe` and then `check` with the journal. A peak within 15% of its ceiling
shows up there. The timer does not raise a limit; use `raise` with the peak as
evidence. A checkout with no `limits.local.toml` skips the audit phase.

Fails (exit 1) when:

- a registry value is above its last approved value (a hand edit),
- a job has limits but no approved baseline, or an approved job is missing,
- the audit log was edited: each entry stores the hash of the previous one, so
  changing or removing a past entry breaks the chain.

It also warns about a job whose recent peak is within 15% of its ceiling, or
reached it, so a limit is raised on evidence before the job is killed rather
than after.

## History

```bash
python3 scripts/resource_limits.py history some-job
```

Each entry shows the change, the reason, and the evidence.
