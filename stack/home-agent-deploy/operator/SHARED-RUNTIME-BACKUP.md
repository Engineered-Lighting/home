# Off-host shared-runtime backup

PostgreSQL backups do not cover the shared-preference services' SQLite
journals, TLS material, service credentials or the internal authority. This
job replicates them independently of pgBackRest and the erasure ledger. It
never uses `off_host_backup_writer.py`, its rclone configuration or the
`HomeAgent/pgBackRest` prefix. It must run and verify once before any link or
consent write is enabled on these services.

## Coverage

The source root is one prepared tree, for example
`/srv/home-agent/shared-preferences/prepared-20260928` (root, 0700). Every run
requires all of these and fails closed if one is missing:

- `echo-preferences`, `victoria-preferences`, `echo-identity`,
  `victoria-identity`, `link-coordinator`, `echo-bff`, `victoria-bff`: each with
  exactly `config/`, `secrets/` and `journals/`;
- `authority/`: internal CA certificate, CA key and role password files.

Other top-level entries in the source root are not covered; the receipt counts
them. Numeric owners and modes are kept in the archive.

Each SQLite journal under `journals/` (`*.sqlite`, `*.sqlite3`, `*.db`, or any
file with a SQLite header) is copied with Python's standard-library SQLite
online-backup API. It opens the source read-only, takes a read transaction
first, and converts the copy to a single-file rollback-journal database. The LA
host has no `sqlite3` CLI. The job waits up to 2 s per attempt and makes five
attempts. A live
file is never copied raw. Each copy must return `ok` for
`PRAGMA integrity_check`. Each journal is internally consistent. There is no
consistency point across journals. A journal with a hot rollback journal fails
closed until its service reopens it. The unit may write the source tree only
so that SQLite can create a WAL index for a read. SQLite gives any sidecar it
creates as root the journal's owner. The job fails if any sidecar's owner
differs from its journal's.

Excluded by name, and never copied:

| Name | Reason |
| --- | --- |
| `sessions.sqlite`, `session.sqlite`, `sessions.db`, `session.db` and their sidecars | BFF session stores are intentionally backup-excluded, like `HOME_AGENT_SESSION_ROOT` |
| `*.owner.sqlite` | Lock-only files held `BEGIN EXCLUSIVE` by the live process; they contain no data |
| `*-wal`, `*-shm`, `*-journal` | Live sidecars; their committed content is captured by `.backup` |

Only these exact session names are excluded. A renamed journal is backed up and
is never skipped. Any other non-SQLite file in `journals/`, any SQLite file in
`config/` or `secrets/`, a symbolic link, or a special file fails the run.

## Archive and transport

The job builds a deterministic tar. Members are sorted, mtimes are zero and no
owner names are stored. The first member, `SHARED-RUNTIME-MANIFEST.sha256`, lists
the SHA-256 of every file. The tar is encrypted with
`openssl cms -encrypt -aes-256-gcm` (OpenSSL 3) to one pinned recipient
certificate. The decryption key must never be on this host or in any archive.
The job keeps plaintext only in a 0700 stage on the encrypted mapper and
deletes it before it exits.

Like the erasure-ledger replicator, the job publishes an immutable epoch
`<remote>:<prefix>/epochs/<YYYYMMDDTHHMMSSZ>/`. It uploads
`shared-runtime.tar.cms` with `rclone copyto --immutable`, then downloads it
again and compares its SHA-256 and size. It writes `complete.sha256` last and
verifies it. The job refuses to run if that epoch or a newer one already
exists. After verification, retention keeps the newest `KEEP` epochs whose
names match the epoch pattern and purges older ones. It never touches other
names under the prefix.

`/srv/home-agent/shared-runtime-backup/receipt.json` (root, 0600) records the
contract, epoch, creation time, member and database counts, plaintext and
ciphertext SHA-256, recipient fingerprint, remote object and
`downloaded_sha256_match`. Output and errors never contain secret values or
file contents.

## Install

Prerequisites: `python3` (standard library only), `openssl` 3.x, `rclone` and `findmnt` in root-owned
system paths. Use a dedicated rclone configuration. It can name the same
approved off-host account, but it keeps its own token so that refreshes never
race the pgBackRest writer or the ledger replicator.

```sh
sudo install -d -o root -g root -m 0755 /usr/local/libexec/home-agent
sudo install -o root -g root -m 0555 \
  stack/home-agent-deploy/operator/replicate_shared_runtime.py \
  /usr/local/libexec/home-agent/shared-runtime-backup.py
sudo install -d -o root -g root -m 0700 \
  /srv/home-agent/shared-runtime-backup \
  /srv/home-agent/secrets/shared-runtime-rclone
# Place rclone.conf there as root:root 0600, and the recipient certificate
# (public only) at /srv/home-agent/config/shared-runtime-backup-recipient.pem
# as root:root 0644.
```

Create `/srv/home-agent/config/shared-runtime-backup.env` as root:root 0600.
Use plain `KEY=value` lines with no quotes or expansion:

```text
HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT=/srv/home-agent/shared-preferences/prepared-20260928
HOME_AGENT_SHARED_RUNTIME_RCLONE_CONFIG=/srv/home-agent/secrets/shared-runtime-rclone/rclone.conf
HOME_AGENT_SHARED_RUNTIME_REMOTE=<approved-remote>
HOME_AGENT_SHARED_RUNTIME_PREFIX=HomeAgent/SharedRuntime
HOME_AGENT_SHARED_RUNTIME_RECIPIENT_CERT=/srv/home-agent/config/shared-runtime-backup-recipient.pem
HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256=<sha256 of the certificate DER>
HOME_AGENT_SHARED_RUNTIME_KEEP=30
HOME_AGENT_EXPECTED_MAPPER=/dev/mapper/home-agent
```

Compute the fingerprint off-host from the reviewed certificate with
`openssl x509 -in recipient.pem -outform DER | sha256sum`. The prefix must not
overlap `HomeAgent/pgBackRest` or any `erasure-ledger` path. Pin the installed
files, then install the units:

```sh
sudo sh -c 'cd / && sha256sum \
  usr/local/libexec/home-agent/shared-runtime-backup.py \
  srv/home-agent/config/shared-runtime-backup.env \
  srv/home-agent/config/shared-runtime-backup-recipient.pem \
  > /srv/home-agent/config/shared-runtime-backup-operator.sha256'
sudo chmod 0600 /srv/home-agent/config/shared-runtime-backup-operator.sha256
sudo install -o root -g root -m 0644 \
  stack/home-agent-deploy/operator/systemd/home-agent-shared-runtime-backup.service \
  stack/home-agent-deploy/operator/systemd/home-agent-shared-runtime-backup.timer \
  /etc/systemd/system/
```

The job refuses to run unless all of these checks pass:

- It runs as root from the installed copy (root:root 0555).
- The trusted parents are root-owned and not group- or world-writable.
- Every manifest digest matches.
- The environment, rclone configuration, source and work roots are on the
  expected mapper.

Any edit to the env file or certificate requires regenerating the manifest.

## First run and verification

```sh
sudo systemctl daemon-reload
sudo systemctl start home-agent-shared-runtime-backup.service
sudo systemctl status home-agent-shared-runtime-backup.service
sudo cat /srv/home-agent/shared-runtime-backup/receipt.json
```

Confirm the receipt epoch and `downloaded_sha256_match`. Confirm that
`database_count` equals the number of journals you expect. Then perform the
restore check below off-host at least once. Only then enable the daily timer
(04:37 plus up to 15 minutes, persistent) and the link or consent writes:

```sh
sudo systemctl enable --now home-agent-shared-runtime-backup.timer
```

Exit status 75 means another run holds the lock. Status 78 means the job
failed closed, and the journal names the reason.

## Restore

Do this on the off-host machine that holds the recipient private key, never on
the LA host:

```sh
rclone copy <remote>:HomeAgent/SharedRuntime/epochs/<epoch> ./epoch
(cd epoch && sha256sum -c complete.sha256)
openssl cms -decrypt -binary -inform DER -in epoch/shared-runtime.tar.cms \
  -inkey recipient.key -recip recipient.pem -out shared-runtime.tar
sha256sum shared-runtime.tar   # must equal the receipt's archive_sha256
mkdir restore && tar -xpf shared-runtime.tar --numeric-owner -C restore
(cd restore && sha256sum -c SHARED-RUNTIME-MANIFEST.sha256)
for db in $(cd restore && awk '{print $2}' SHARED-RUNTIME-MANIFEST.sha256 | grep '/journals/'); do
  test "$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0])' "restore/$db")" = ok || echo "FAILED $db"
done
```

Transfer the verified tree to the host over an approved channel. Stop the
affected services first. Restore each service directory with its numeric
owners and modes. Do not recreate session stores; users sign in again. Restored
journals are older than the live state. Reconcile them with the database and
the erasure ledger before re-enabling writes. Never use a restore as casual
application rollback.
