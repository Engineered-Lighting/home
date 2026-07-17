# Home Agent PostgreSQL archive incident — 2026-07-16

## Outcome

The Home Agent PostgreSQL deployment entered automatic crash recovery roughly
once per minute while its off-host pgBackRest SFTP repository was unavailable.
The Home Agent API, ingest, worker, and PostgreSQL containers were stopped to
end the recovery loop. The Ubuntu host remained online at low load and logged
no new kernel machine-check, thermal, OOM, panic, or I/O event during the
incident.

This incident is separate from the 2026-07-12 host halt. It did not run the
quarantined E1/E2 PostgreSQL gate and did not create disposable Docker test
stacks.

## Evidence chain

The PostgreSQL container ran PostgreSQL itself as PID 1 while pgBackRest used
asynchronous WAL archiving. The configured SFTP backup peer had been offline
since 2026-07-15. Each sequence was:

1. `archive-push:async` waited one minute for the unavailable repository.
2. pgBackRest reported archive failure code 82.
3. A detached process exited with code 103.
4. PostgreSQL PID 1 reaped that process as a server process, terminated every
   active backend, and performed automatic recovery.
5. Recovery truncated the intentionally unlogged worker-maintenance lease.
6. The worker observed `worker maintenance instance is not current`, exited,
   and Docker restarted it.

The worker accumulated 375 restarts, but consumed about 1.5 seconds of CPU per
cycle and was not the source of the database crash. Stopping the worker did not
stop PostgreSQL recovery; stopping PostgreSQL did.

## Correction

The PostgreSQL Compose service now sets `init: true`. Docker's minimal init
process owns PID 1, forwards signals, and reaps detached descendants, so
PostgreSQL only supervises its actual children. WAL archive failures remain
fail-closed: PostgreSQL retains unarchived WAL and reports backup degradation;
the change does not acknowledge, discard, or fake successful archiving.

The deployment also locks `archive-push-queue-max=0B`. A positive limit would
permit pgBackRest to acknowledge and drop queued WAL after that limit is
crossed, breaking point-in-time recovery. Preflight rejects a missing,
duplicate, or nonzero setting and also locks the encrypted spool path and
bounded archive worker count.

The deployment stays stopped until the corrected container topology is
reviewed and the following low-impact recovery checks can run with the backup
peer intentionally unreachable:

- PostgreSQL remains continuously available for at least three archive retry
  windows.
- No `all server processes terminated; reinitializing` record appears.
- The unarchived WAL remains present and the archive status remains failed.
- Restoring the SFTP peer drains the retained WAL before the deployment is
  considered backup-healthy.

An unavailable archive repository therefore creates deliberate WAL and disk
pressure. Operators monitor `pg_stat_archiver`, retained `pg_wal` bytes, and
encrypted-volume free space. At 15% free space, optional ingest stays stopped;
at 10%, the deployment remains read-only/degraded. Operators must never delete
WAL, remove `.ready` markers, or mark a failed archive as successful. The core
worker remains stopped until its separate lease-arbitration defense is
reviewed and deployed.

No raw log, credential, repository cipher secret, private key, or exact backup
host configuration belongs in this repository.
