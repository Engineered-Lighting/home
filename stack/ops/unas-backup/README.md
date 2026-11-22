# LA AI host backup to the Victoria UNAS

These files mirror what is installed on `home-app` (EngineeredLightingServer1).
They were set up on 2026-11-13 from a workstation workspace that no longer
exists. Phase 1 went live on 2026-11-22 with owner approval.

| Repo file | Installed at |
|---|---|
| `backup.py` | `/opt/unas-linux-backup/backup.py` |
| `backup.json` | `/etc/unas-linux-backup/backup.json` |
| `Backup-LAHaosConfig.sh` | `/opt/unas-haos/Backup-LAHaosConfig.sh` |
| `systemd/*` | `/etc/systemd/system/` |
| `dropins/unas-linux-backup@.service.d-read-access.conf` | `/etc/systemd/system/unas-linux-backup@.service.d/read-access.conf` |
| `dropins/onfailure-ntfy.conf` | `<unit>.d/onfailure-ntfy.conf` for `unas-linux-backup@.service`, `unas-linux-backup-freshness.service`, `unas-la-haos-config.service`, `home-agent-shared-runtime-backup.service` |

The credentials (`/etc/unas-linux-backup/restic-password` and `rclone.conf`, an
SMB remote named `unas`) are created by the owner. They are never stored in
this repo.

## Schedule

| Timer | When | Job |
|---|---|---|
| `unas-linux-backup.timer` | nightly 02:45 PT | `backup.py --mode backup` (restic, 4 MiB/s cap, Nice 15, idle IO) |
| `unas-linux-backup-check.timer` | Sun 16:00 PT | `restic check` |
| `unas-linux-backup-freshness.timer` | hourly | fails, and alerts through ntfy, if the last success is older than 36 h |
| `unas-la-haos-config.timer` | daily 05:20 PT | HA config subset, then `restore --verify` |

## Selection

`backup.json` is the allowlist. It covers:

- `/srv/data/ha-backups`, except `incoming/`;
- `/var/lib/private/den-live`;
- `/srv/data/label-backups`;
- operator state: systemd units and drop-ins, `/usr/local/sbin`, `/usr/local/libexec`, the UNAS scripts, `/etc/home-agent`, crontabs and `fstab`.

The backup user gets `CAP_DAC_READ_SEARCH` so it can read those paths.
`backup.py` checks readability with `effective_ids=True` so that the capability
counts. It always excludes its own credentials, state and cache.

Out of scope here:

- Home Agent PostgreSQL, the erasure ledger and shared runtime, which have their own jobs under `stack/home-agent-deploy/operator/`;
- secrets;
- model and trial data.

## Restore drill

Restore the newest HA daily tar from the latest snapshot into a scratch
directory. Its SHA-256 must equal the `sha256` in
`/srv/home-agent/config/haos-backup-last-daily.json`. Run the restore in a
transient unit that loads the same two credentials, so they never appear in a
shell.
