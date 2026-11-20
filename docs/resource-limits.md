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

## Checking

```bash
python3 scripts/resource_limits.py check
```

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
