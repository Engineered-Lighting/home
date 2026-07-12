# Isolated PostgreSQL restore drill

This drill proves that a completed encrypted off-host backup can be staged,
decrypted, recovered, queried, dumped, shut down, and checksum-verified without
touching the production cluster or opening a database port.

The drill intentionally does **not** restore directly from pgBackRest's
libssh2 SFTP backend. Parallel libssh2 restores have left worker processes
hung after `EAGAIN`/`BAD_USE` transport failures against the Windows OpenSSH
repository. Instead, native OpenSSH `sftp` copies the encrypted repository to
temporary encrypted storage. The selected backup is then verified and restored
from a local read-only POSIX repository with Docker `network_mode=none`.

## Required deployment inputs

Run `preflight.sh` after configuring these non-secret values in
`home-agent.env`:

```text
HOME_AGENT_RESTORE_DRILL_ROOT=/srv/home-agent/restore-drills
HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS=/srv/home-agent/backup-sftp/formd_known_hosts
HOME_AGENT_PGBACKREST_IMAGE=engineered-lighting/home-agent-postgres:17.10
HOME_AGENT_EXPECTED_DB_REVISION=0006_worker_maintenance_health
```

The restore root and `known_hosts` file must be on the encrypted mapper. The
known-host entry must be obtained through a separately authenticated channel;
the drill never runs `ssh-keyscan` and requires `StrictHostKeyChecking=yes`.
The root-owned OpenSSH `known_hosts` entry and pgBackRest's configured SHA-256
fingerprint are independent pins. They may identify different valid server
host-key algorithms and are therefore validated independently rather than
incorrectly requiring their digest strings to match.

The custom PostgreSQL image is referenced by a tag in the environment only so
Compose can build it. At drill start the script resolves that tag to an
immutable local image ID and verifies its OCI base label exactly equals the
digest-pinned `HOME_AGENT_POSTGRES_IMAGE`.

Production `pgbackrest.conf` must also set `repo1-bundle=y`. Bundling applies to
new backups and reduces the large number of small SFTP file operations that
exposed the libssh2 failure. It does not weaken encryption or replace the
native OpenSSH staging path used by this drill.

## Run

Choose an explicit completed pgBackRest label from the backup report. Do not
use `latest`; a drill must remain attributable to one immutable backup set.
The Compose `backup-gate` intentionally runs before Alembic, so its deployment
backup proves the rollback boundary at the previous revision. After a migration
and healthy service replacement, run `backup-gate` once more to create a
post-migration full backup and use that new immutable label for the acceptance
drill. A pre-migration backup must fail the new revision contract; it is not a
substitute for this post-migration proof.

```sh
cd /opt/home/home-github/stack
sudo bash home-agent-deploy/operator/isolated_restore_drill.sh \
  /srv/home-agent/config/home-agent.env \
  20260711-220110F
```

The script takes an exclusive lock and performs these stages:

1. Validate root ownership, mapper placement, exact path separation, minimum
   free space, source-file modes, image digests, and the known-host fingerprint.
2. Copy the SFTP private key to a mode-0600 file inside the encrypted drill
   workspace and stage the encrypted repository with native OpenSSH batch SFTP.
3. Reject links and special files, then verify the requested backup using a
   local read-only POSIX pgBackRest repository.
4. Restore as UID/GID 999 with immediate recovery, promotion, and archive mode
   disabled.
5. Compare the restored and production PostgreSQL system identifiers.
6. Boot PostgreSQL with `network_mode=none`, no published ports, an empty
   `listen_addresses`, and a tmpfs pgBackRest spool used only for local WAL
   recovery.
7. Require the pinned PostgreSQL version, page checksums, expected Alembic
   revision, all seven domain schemas, and a successful schema-only `pg_dump`.
8. Stop PostgreSQL cleanly and run offline `pg_checksums --check` across the
   restored cluster.
9. Remove named containers and the exact guarded workspace. The temporary SSH
   key and cipher-bearing local config are scrubbed on every exit path.

No backup passphrase, database password, or private key is placed in an
argument, environment variable, Docker inspection record, or log line.

## Failure handling

By default, failures remove the temporary workspace after scrubbing credential
material. Set this only for a supervised investigation:

```text
HOME_AGENT_RESTORE_KEEP_FAILED=1
```

When enabled, the staged encrypted repository and partial restored cluster stay
under the mode-0700 encrypted drill root, but the temporary private key and
local pgBackRest config are still removed. Delete retained workspaces only
after resolving their canonical path and confirming their nearest mount is the
approved encrypted restore mount. Never use a wildcard recursive removal.

Optional timeout and capacity inputs are:

```text
HOME_AGENT_RESTORE_SFTP_TIMEOUT_SECONDS=7200
HOME_AGENT_RESTORE_PHASE_TIMEOUT_SECONDS=3600
HOME_AGENT_RESTORE_MIN_FREE_KIB=2097152
```

This database drill does not replace the independent erasure-ledger replay gate
or a full isolated Home Assistant application restore.

## Monthly execution

After one supervised drill passes, install `monthly_restore_drill.sh` and the
matching service/timer templates from `operator/systemd`. The selector queries
pgBackRest for the newest *completed full* backup, validates its immutable
label, writes that label to the service journal, and invokes this same guarded
operator. It never passes `latest` to a restore command.

The timer runs on the first Sunday of each month after the normal daily backup
window. A failed drill remains a failed systemd unit and must be investigated;
the timer does not weaken cleanup, networking, checksum, or erasure-replay
requirements.
