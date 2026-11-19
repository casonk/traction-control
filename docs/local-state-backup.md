# Backing up local-only state

Git holds the tracked code. Everything a repository keeps out of Git on purpose (real settings, credentials, databases,
downloaded records, vaults) exists on one machine only until something copies it. This is the standard way the portfolio
does that. Two scheduled jobs cover two kinds of state; the audit fails when either stops.

| | Local configuration bundle | Dataset backups |
|---|---|---|
| What | every Git-ignored `*.local.*`, `.env*`, `REFS-LOCAL.md`, `secrets/` file; SSH and GPG keys; the clockwork certificate authority; Terraform state | databases, record archives, vaults, session memory: anything too large for a config store |
| Size | hundreds of KB | gigabytes |
| Script | `scripts/local_state_bundle.py` | `scripts/state_dataset_backup.py` |
| Where it goes | one age-encrypted payload, committed by the private encrypted-config store to its `local-state-backups` branch, so a copy leaves the machine | age-encrypted, checksummed, versioned files on a spinning disk (`docs/bulk-storage.md`) |
| Schedule | daily 03:30 (`local-state-backup.timer`) | daily 04:15 (`state-dataset-backup.timer`) |
| Declaration | none: files are discovered, so a new local file is covered the next night | `config/state-datasets.local.toml` (ignored) |

## Local configuration bundle

`local_state_bundle.py backup` discovers the files, writes one deterministic `tar.gz` (sorted, zeroed times and owners,
so unchanged content gives identical bytes), skips the run when nothing changed, and otherwise has the store capture it.
The store encrypts with age to its public recipients, so the job needs no secret. The commit lands on a separate worktree
of the store repository on the `local-state-backups` branch and is pushed there. Main never carries a payload.

- Discovery reads each repository's ignored files and keeps those matching the local-file patterns, plus a fixed list of
  home paths. Optional extras go in `config/local-state.local.toml` (`home_paths`, `extra_files`).
- The store's location is local configuration, never tracked: `config/local-state.local.toml` (see
  `config/local-state.toml.example`) sets `store_repo`, `store_script` and `store_root_env`.
- A file over 256 KiB, a non-regular file, or one this account cannot read is reported as skipped by `status`, never
  silently dropped and never fatal.
- Restore: `python3 scripts/local_state_bundle.py restore ARCHIVE` is a dry run; `--apply` writes files that do not exist,
  `--force` overwrites. Names with `..`, absolute names, links and checksum mismatches are refused. To get the archive,
  check out the branch, then the store's `restore --id local-state-bundle` command (needs the age identity).

## Dataset backups

Each `[[dataset]]` is one `tar.zst.age` generation per run. SQLite files are copied through SQLite's backup API (a
consistent snapshot), never as raw files. A dataset whose files have not changed since the newest generation is skipped.
After writing, the backup is decrypted and read end to end; only a verified generation counts, and the oldest generations
beyond `keep` are then removed. The destination must be a spinning disk and outside every Git worktree. Plaintext
metadata is limited to counts, sizes, a fingerprint and the ciphertext checksum, never file names.

```bash
python3 scripts/state_dataset_backup.py plan                  # what would be backed up, and what changed
python3 scripts/state_dataset_backup.py backup [NAME ...]
python3 scripts/state_dataset_backup.py verify                # decrypt and read the newest generation of each
python3 scripts/state_dataset_backup.py restore NAME --to DIR # into a new, empty directory only
```

Start from `config/state-datasets.toml.example`. Keep the real list ignored: it names private repositories and paths.

## The key is the single point of failure

Both jobs encrypt to the store's global age recipient, and the matching identity file lives
on this machine. If the machine and every copy of that identity are lost, **every encrypted backup is unreadable**. Keep
an offline copy of the identity (and add a recovery recipient to the store's global layer, which its `capture` already
warns about). A backup nobody can decrypt is not a backup.

The dataset backups on the local HDD protect against a failed drive, not against loss of the machine or the site. Only
the configuration bundle leaves the machine today. Moving dataset generations offsite is a deliberate later step; it
needs client-side encryption (already true) and a destination decision.

## Why not the portfolio sidecar or restic

The private sidecar's schema accepts only datasets from registry-public repositories and writes only to SFTP targets, so
it cannot hold these private datasets or write to a local disk. restic is not installed. This design needs age and zstd,
and each generation is one self-contained file.

## Checks

`scripts/portfolio-audit.sh` fails when the bundle is older than two days (`LOCAL_STATE_MAX_AGE_DAYS`), or a dataset's
newest generation is missing, unverified or older than three days (`DATASET_BACKUP_MAX_AGE_DAYS`). It skips the dataset
check, without counting it, on a machine with no dataset config.

Rehearse a restore after any change to the job and at least once a quarter: restore into a scratch directory and compare
(for databases, run `PRAGMA integrity_check`).
