# Isolated PostgreSQL restore drill

This drill proves that a completed encrypted local or legacy off-host backup can
be recovered, queried, dumped, shut down, and checksum-verified without
touching the production cluster or opening a database port.

For legacy recovery, the drill intentionally does **not** restore directly from
pgBackRest's libssh2 SFTP backend. Parallel libssh2 restores have left workers
hung after `EAGAIN`/`BAD_USE` transport failures against the Windows OpenSSH
repository. Instead, native OpenSSH `sftp` copies the encrypted repository to
temporary encrypted storage. The selected backup is then verified and restored
from a local read-only POSIX repository with Docker `network_mode=none`. Active
`local` mode briefly takes the deployment's exclusive repository lock and
copies encrypted repo1 into the guarded restore workspace. PostgreSQL may remain
online because its archive command and every reviewed backup writer take the
same lock. The lock is released before verification and restore begin.

## Required deployment inputs

Run `preflight.sh` after configuring these non-secret values in
`home-agent.env`:

```text
HOME_AGENT_RESTORE_DRILL_ROOT=/srv/home-agent/restore-drills
HOME_AGENT_BACKUP_TOPOLOGY=local
HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT=/srv/home-agent/durable/pgbackrest-repository
HOME_AGENT_PGBACKREST_IMAGE=engineered-lighting/home-agent-postgres:17.10
HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration
```

The restore root and local repository must be on the encrypted mapper. For an
offline `sftp_legacy` recovery, also configure the SFTP key and `known_hosts`;
the
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

The script takes an exclusive workspace lock and performs these stages:

1. Validate root ownership, mapper placement, exact path separation, minimum
   free space, source-file modes, image digests, and the known-host fingerprint.
2. For local repo1, reject unreviewed containers that mount protected storage,
   verify the running production PostgreSQL container's exact data, repository,
   and coordination-lock mounts, then stage the encrypted repository while
   holding that lock. Alternatively, copy a legacy SFTP repository with native
   OpenSSH batch SFTP.
3. Reject special files and every link except pgBackRest's exact
   `backup/home-agent/latest` symlink. That one link must use a relative full
   backup label and resolve to an existing non-symlink directory in the same
   stanza before the requested backup is verified from the local read-only
   POSIX repository.
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
9. Atomically write the root-owned, mode-`0600`
   `/srv/home-agent/config/phase3-restore-drill-e5j.json` receipt, bound to the
   immutable backup label, restored database system identifier, and exact
   source schema revision.
10. Remove named containers and the exact guarded workspace. The temporary SSH
   key and cipher-bearing local config are scrubbed on every exit path.

No backup passphrase, database password, or private key is placed in an
argument, environment variable, Docker inspection record, or log line.

## Returning production to service

The drill does not restart production. From the same reviewed deployment
checkout, refresh the root-owned runtime secret copies with `preflight.sh`,
then use the normal staged startup sequence:

1. Start PostgreSQL alone with `up -d --no-deps --no-build --pull never` and
   require it to become healthy.
2. Run fresh, disposable `provision-roles`, `backup-gate`, `migrate`, and
   `grant-runtime` containers in that order with
   `run --rm --no-deps --pull never`. Stop if any one-shot fails.
3. Start `core-api`, `core-worker`, `core-ingest`, `edge-ingress`, and `bff`
   separately with `up -d --no-deps --no-build --pull never`, checking each
   service before advancing.
4. Re-verify the BFF firewall contract, then restart the local backup timer.

Do not use `docker compose start core-api` (or another dependent long-running
service) as the recovery shortcut. Compose may then execute an old stopped
one-shot container whose secret mounts predate the reviewed deployment.

## Failure handling

By default, failures remove the temporary workspace after scrubbing credential
material. Set this only for a supervised investigation:

```text
HOME_AGENT_RESTORE_KEEP_FAILED=1
```

When enabled, the partial restored cluster and staged encrypted repository stay
under the mode-0700 encrypted drill root, but temporary credential material is
still removed. The active local repository is never chowned, chmodded, or
deleted by cleanup. Delete retained workspaces only after resolving their
canonical path and confirming their nearest mount is the approved encrypted
restore mount. Never use a wildcard recursive removal.

Optional timeout and capacity inputs are:

```text
HOME_AGENT_RESTORE_SFTP_TIMEOUT_SECONDS=7200
HOME_AGENT_RESTORE_PHASE_TIMEOUT_SECONDS=3600
HOME_AGENT_RESTORE_MIN_FREE_KIB=2097152
```

This database drill does not replace the independent erasure-ledger replay gate
or a full isolated Home Assistant application restore. Its receipt deliberately
contains no erasure-replay claim. After independently establishing that the
database and external ledger are current, the root operator may run the E5j
`erasure-current` receipt ceremony documented in `HOME-AGENT-RUNBOOK.md`.

## Monthly execution

After one supervised drill passes on an operator-approved restore host, install
`monthly_restore_drill.sh` and the matching service/timer templates from
`operator/systemd`. The selector queries pgBackRest for the newest *completed
full* backup, validates its immutable label, writes that label to the service
journal, and invokes this same guarded operator. It never passes `latest` to a
restore command.

`EngineeredLightingServer1` / `home-app` remains quarantined from the scheduled
restore workload after the 2026-07-12 unclean halt. The systemd unit rejects
both names before process start, and the selector independently checks the
process hostname and Docker daemon name. There is no environment override for
that scheduled path.

The local-topology drill can now query a running PostgreSQL container, stage a
coherent encrypted repository snapshot behind the shared coordination lock,
and leave production online. The scheduled path remains disabled on
`EngineeredLightingServer1` / `home-app`; this change does not lift the E1/E2
runner, image-build, stress-test, or scheduled-restore quarantines. On that host
the path remains a supervised, resource-bounded manual recovery proof.

The timer runs on the first Sunday of each month after the normal daily backup
window. It is deliberately non-persistent: a missed run does not catch up
automatically during boot or crash recovery. CPU, memory, swap, task, file
descriptor, and I/O limits bound the selector and staging process; each Docker
container retains its own stricter CPU, memory, and PID limits. A failed or
missed drill must be investigated and rescheduled on the approved restore host;
the timer does not weaken cleanup, networking, checksum, or erasure-replay
requirements.
