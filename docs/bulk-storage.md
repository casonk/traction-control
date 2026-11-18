# Bulk storage belongs on a spinning disk

Any process that writes or keeps large, mostly sequential data (downloads, backups, archives, dataset mirrors, model
stores, raw disk exports) puts it on an HDD, not an SSD or NVMe drive. Flash has a finite write endurance and a fixed
capacity that the working system needs; a spinning disk streams large files well enough and has room to spare. Bulk data
also needs a second copy: a single copy on any one disk is not a backup.

## The rule

1. **Choose the medium before the first write.** A new bulk writer's default destination is on an HDD. Check with
   `lsblk -s -o NAME,TYPE,ROTA <device>` (`ROTA` 1 is a spinning disk, 0 is flash). Resolve symlinks and the
   encryption layer first: a path that looks like an HDD mount can be a link to flash.
2. **Scripts that write bulk data refuse flash.** They look up the destination's physical disk, treat an unidentifiable
   disk as flash (fail closed), and offer an explicit override such as `--allow-flash` for the rare justified case.
   `scripts/backup_azure_runner_images.sh` is the reference implementation (`disk_kind`).
3. **Keep a working set on flash, the archive on HDD.** A job that needs the speed of NVMe reads a subset there. The full
   dataset, and its second copy, live on the HDD.
4. **Verify before you delete.** Moving bulk data off flash means copy, checksum-compare, then remove the original only
   with the owner's go-ahead.
5. **Declare it.** Configured bulk destinations are listed (or discovered from env files) so the audit can see them.

## The audit check

`scripts/check_bulk_storage.py` runs inside `scripts/portfolio-audit.sh` and reports each configured bulk path on flash
or on a medium it cannot identify. It reads:

- every `*.env.local` / `*.env` file under the portfolio root, for variables ending in `_DOWNLOAD_DIR`,
  `_BACKUP_DIR`, `_BACKUP_DEST`, `_ARCHIVE_DIR`, `_DATASET_DIR` or `_MODELS_DIR` that hold an absolute path;
- `config/bulk-storage.local.toml` (ignored; start from `config/bulk-storage.toml.example`) for paths that are bulk by
  declaration, and an `allow_flash` list for deliberate exceptions.

Name a new bulk setting with one of those suffixes and the audit covers it with no further wiring. Keep the local file
free of anything that must not appear in a public repository: it is ignored, and the tracked example is synthetic.

## When flash is correct

Scratch space a job rewrites constantly, databases with random I/O, and the system's own caches are not bulk archives and
belong on flash. If a path is on flash on purpose, add it to `allow_flash` with a comment saying why.
