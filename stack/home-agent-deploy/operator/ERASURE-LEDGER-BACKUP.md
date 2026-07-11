# Off-host erasure-ledger replication

The erasure ledger is backed up independently from PostgreSQL so a database
restore cannot resurrect content erased after the selected backup. The
replicator publishes an immutable, versioned epoch first and writes its
`complete.sha256` commit marker last. Only then does it update the repairable
`current` view.

Install the script and unit templates as root:

```sh
install -o root -g root -m 0755 \
  stack/home-agent-deploy/operator/replicate_erasure_ledger.sh \
  /usr/local/sbin/home-agent-ledger-backup
install -o root -g root -m 0644 \
  stack/home-agent-deploy/operator/systemd/home-agent-ledger-backup.service \
  /etc/systemd/system/home-agent-ledger-backup.service
install -o root -g root -m 0644 \
  stack/home-agent-deploy/operator/systemd/home-agent-ledger-backup.timer \
  /etc/systemd/system/home-agent-ledger-backup.timer
install -d -o root -g root -m 0700 /srv/home-agent/ledger-replication
```

Store the dedicated rclone configuration on the encrypted mapper, mode 0600,
because it may contain a refresh credential and rclone may rotate it. Create
`/etc/home-agent/ledger-backup.env` as root, mode 0600:

```text
HOME_AGENT_RCLONE_CONFIG=/srv/home-agent/config/rclone.conf
HOME_AGENT_ERASURE_LEDGER_REMOTE=<approved-remote>:<private-path>/erasure-ledger
HOME_AGENT_ERASURE_LEDGER_ROOT=/srv/home-agent/erasure-ledger
HOME_AGENT_STAGE_ROOT=/srv/home-agent/ledger-replication
HOME_AGENT_EXPECTED_MAPPER=/dev/mapper/home-agent
```

The dedicated root-owned staging directory is a required service condition;
the service does not receive write access to the rest of `/srv/home-agent`.

Enable the timer only after one supervised run succeeds:

```sh
systemctl daemon-reload
systemctl start home-agent-ledger-backup.service
systemctl enable --now home-agent-ledger-backup.timer
```

The script takes both a process lock and Core's ledger lock, snapshots the
ledger and head together, validates the head shape, and verifies remote object
hashes. A matching `current/complete.sha256` is the normal idempotent fast path.
A partially uploaded epoch is resumed only when its existing object hashes
match; collisions fail closed. Logs contain operation errors only, never ledger
content.

During recovery, select the immutable epoch corresponding to the required
ledger head. Do not treat `current` as an independent authority. PostgreSQL and
application services must remain quarantined until every erasure epoch later
than the restored database has been replayed and receipted.
