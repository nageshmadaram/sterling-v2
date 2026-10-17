# Backup and restore

## The database (do this first)

The main database (the file named by `STERLING_DB_PATH`) holds the Kite account,
the order journal and open positions. Back it up every day after the close:

```bash
./sterlingctl backup          # checksummed backup with a manifest
./sterlingctl restore-check   # restores into a temp directory and verifies it
```

A full release restore also requires **physical frozen lane artifacts**, not just
a readable database and a `release.json` summary. On the deployed state root,
check `data/manifests/release.json` and
`data/manifests/strategies/*.json` with `scripts/sterlingctl verify`.
The command now fails if a lane file is missing, extra or altered.

If the lane files were never frozen, **do not manufacture matching files from
a different build**. Finish the exact-SHA release process in
[FINAL_RELEASE_RUNBOOK.md](FINAL_RELEASE_RUNBOOK.md), freeze on the deployed
build, then take a new **post-freeze** backup and run `restore-check`.
If files from an earlier frozen release were lost, restore them from
the corresponding verified backup and investigate the loss. A pre-freeze
database-only restore is not full deployment recovery.

Never delete, rename, replace or initialize `sterling_paper.db` as a
cleanup or migration shortcut. Its legacy name is the current durable
order journal; only a verified, reversible migration may move state.

`restore-check` never touches the live database. A backup that has never been
restored is a hypothesis. After a real restore, reconcile against the broker
(`./sterlingctl reconcile`) before allowing new entries: the restored journal may
be older than what the broker holds. Keep one copy off this machine.

## The market-data lake: why it matters

Sterling records market data that cannot be bought back later. The data vendor
does not sell historical prices for expired option contracts — this was verified
directly, and it is why the historical study had to use estimated prices instead
of real ones for all 746 signals.

Every day Sterling records is permanent evidence. Every day it fails to record
is permanently gone.

## What exists today

The market data lake lives on a USB drive, with two verified copies:

```
working    /run/media/.../SterlingLake
copy 1     /mnt/OS/SterlingLakeBackup          (internal disk)
copy 2     the SD card                          (separate device)
```

Both were verified file-by-file with checksums, not just copied. A successful
copy is not evidence that the files survived intact.

## Making a new backup

```bash
LAKE=$(ls -d /run/media/*/*/SterlingLake)
DEST=/mnt/OS/SterlingLakeBackup

rsync -a --info=stats2 "$LAKE/" "$DEST/SterlingLake/"
```

Then **verify it**, which is the part people skip:

```bash
cd "$DEST/SterlingLake" && sha256sum -c "$DEST/LAKE_MANIFEST.sha256" --quiet && echo VERIFIED
```

If that prints VERIFIED, the copy is real. If it prints anything else, the copy
is not trustworthy and the original is still your only copy.

## Regenerating the manifest after new evidence is recorded

```bash
LAKE=$(ls -d /run/media/*/*/SterlingLake)
cd "$LAKE" && find . -type f -print0 | sort -z | xargs -0 sha256sum > /tmp/LAKE_MANIFEST.sha256
```

Keep the manifest with each copy. Record, each time: the date, the number of
files, and whether verification passed.

## Restoring

Copy back from a verified backup, then verify the restored copy against the same
manifest before using it. Do not skip that check because the copy "looked fine".

## If the drive dies

The two backups are the system. Replace the drive, restore from a verified copy,
verify again, and continue. Nothing is lost provided the backups were verified
when they were made — which is the entire reason for verifying them.
