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
Automatic restart is disabled for commissioning. Inspect TLS and authenticated
operation results; this configuration does not claim application readiness.

Rollback stops these new services and revokes their new credentials while
retaining journals, current data and deletion history. It must not restore an
older database. No live acceptance has been performed with this configuration.
