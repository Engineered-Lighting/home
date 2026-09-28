# Private shared-preference services

`compose.json` is a separate, opt-in Compose project. It adds two preference
listeners, two identity listeners and one linking coordinator. It does not
replace Core, BFF, the web gateway or Lab, and it does not run migrations.

Before startup, provide a reviewed environment file containing every required
variable. No production addresses, image tags or resource budgets are defaulted.
Use the immutable Core image produced by the approved release workflow.

Each service needs its own pre-created directories, accessible to UID 10001:

- `*_SECRETS`: mounted read-only at `/run/secrets`; contains only that service's
  API database URL, knowledge key, service token and dedicated profile secrets.
  Never mount the global secret directory or administrator database credentials.
- `*_CONFIG`: mounted read-only at `/config`; includes `listener.json` and TLS
  material, matching the existing role-specific profile loader.
- `*_JOURNALS`: mounted writable at `/journals`; use separate encrypted durable
  storage for each service. Include journals and their recovery keys in the
  reviewed backup and retention procedure before enabling writes.

The listener address in each profile must match its reserved `*_IP` on the
existing API network. Profiles reference container paths, not host paths.
Database URLs must resolve the existing PostgreSQL service on its private
network. There are no host-published ports. BFF routes require private TLS and
dedicated service credentials; browser origins remain separately provisioned.

Apply `core-pool-limits.json` as the last override in the existing Core project's
reviewed Compose command. It bounds the main API, ingest and worker pools to two
connections each without overflow, and the API operator pool to two. Existing
three binding/relationship/owner pools remain at two each. The legacy service
total is therefore 14. This requires the new image; older images ignore these
settings. Recreate existing services without overlapping old/new instances and
verify the live settings before admitting the new services.

The new five-service configuration is bounded to another 14 connections:
Echo preferences 2, Victoria preferences 1, each identity service 3, coordinator
5. This includes auxiliary role pools. With one instance of each service, the
total is 28, leaving nine ordinary slots plus three PostgreSQL reserved slots
on the inspected 40-connection database. Admit maintenance jobs against the
nine-slot remainder; no overlapping migration, restore or duplicated runtime
is implicitly admitted. Verify actual process count and role limits at startup.
The observed eight live connections are not evidence of peak capacity, and
these limits still require live latency/error acceptance.

Validate resolved configuration without starting services:

```sh
docker compose --env-file /absolute/reviewed.env -f compose.json --profile shared-preferences config --quiet
```

Validate current host health, compatible schema 0047, installed permissions,
verified backup and rollback artifacts before a coordinated startup. Start only
the explicitly admitted service names with `up -d --no-build --no-deps`.
The five live services (`echo-preferences`, `echo-identity`, `victoria-identity`,
`link-coordinator`, `victoria-link`) restart `unless-stopped`, after live
acceptance on 2026-09-28 and a sustained resource check. `victoria-preferences`
has no client yet, so it stays staged with `restart: no`. Inspect TLS and
authenticated operation results after any restart.

Rollback stops these new services and revokes their new credentials while
retaining journals, current data and deletion history. It must not restore an
older database. Live acceptance passed on 2026-09-28; see `docs/SHARED-PREFERENCES-RELEASE-2026-09-28.md`.

## Migration 0031 -> 0047

`operator/shared_preferences_migration.py` migrates live Core from
`0031_relationship_uniqueness_e5r` to `0047_personal_pref_authority_v1` and
nothing else. It runs the image's fixed `phase3-migrate-personal-preferences`
entrypoint once; it never builds, pulls, starts dependencies, applies grants
or restores a database. Install it into a root-owned path; never run it from
the mutable checkout. The digest comparison closes the copy race:

```sh
cd /opt/home/home-github
expected="$(git hash-object stack/home-agent-deploy/operator/shared_preferences_migration.py)"
sudo install -d -m 0755 -o root -g root /usr/local/libexec/home-agent/shared-preferences
sudo install -m 0555 -o root -g root \
  stack/home-agent-deploy/operator/shared_preferences_migration.py \
  /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py
installed="$(sudo git hash-object \
  /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py)"
test "$installed" = "$expected"
```

Every input must be an absolute root-owned path with no group/world-writable
component (including the Compose file's directories). The receipt directory is
`root:root 0700`. `core-runtime-0047.json` must pin `--core-image` for all three
Core roles with readiness `0047`; `core-runtime-0031.json` is the reviewed
current runtime, used only if the migration fails before commit. Run `check`
first, then `run` with identical arguments within the two-hour off-host backup
window:

```sh
OVERRIDES=/absolute/reviewed/overrides
set -- \
  --env /srv/home-agent/config/home-agent.env \
  --compose-file /opt/home/home-agent/stack/home-agent-compose.yml \
  --pool-limits "$OVERRIDES/core-pool-limits.json" \
  --runtime-override "$OVERRIDES/core-runtime-0047.json" \
  --rollback-override "$OVERRIDES/core-runtime-0031.json" \
  --core-image sha256:<64-hex verified Core image ID> \
  --receipt-dir /srv/home-agent/shared-preferences/migration-receipts
sudo python3 -I /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py check "$@"
sudo python3 -I /usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py run "$@"
```

Both actions hold the exclusive activation lock and require: Core ready at
0031 with rollout authorized and current restore gate and worker maintenance;
healthy Core roles and PostgreSQL; an off-host receipt for the newest full
pgBackRest backup copied within two hours; a successful maintenance restore
receipt; a present, labelled Core image; and `HOME_AGENT_RUN_MIGRATIONS` not `1`.
`check` exits 0 or 3 (blocked) without changing anything.

`run` stops worker, ingest and API, migrates through the `migrate` service
bounded to 0.5 CPU, 512 MiB and 64 PIDs, then starts only the three roles with
the pool limits and 0047 runtime and waits up to 180 s for readiness at 0047.
It writes a `0400` receipt `shared-preferences-migration-<UTC>.json`. Exit 0 is
`migrated`. Exit 4 (`failed_rolled_back`) means the migration transaction
rolled back, the source revision was verified and Core is ready again at 0031.
Exit 5 needs attention: after `migrated_core_unready` never start the 0031
image or restore the database; fix forward, or use the owner-approved reviewed
`alembic downgrade 0031_relationship_uniqueness_e5r` only before source
registration or CONNECT grants. After `migration_state_unverified` Core roles
remain stopped until the live revision is verified. `failed_source_unready`
means the schema stayed at 0031 but the restarted 0031 runtime is not ready.
