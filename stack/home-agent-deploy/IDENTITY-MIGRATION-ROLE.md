# Identity migration database roles

The Phase 3 deployment provisions two deliberately separate roles:

- `home_agent_identity_migration` is a `NOINHERIT`, `NOBYPASSRLS`,
  connection-limit-one login used only by the private operator profile.
- `home_agent_identity_kernel` is a `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS`
  owner for reviewed `SECURITY DEFINER` kernels. It has no database login or
  default privileges and is never granted to the migration login or a runtime
  role.

Revision 0007 leaves both roles inert. Revision 0008 may expose only the
manifest-only capability and registration kernels; finalization, semantic
projection, parent facts, and cutover remain disabled.

Provisioning always sets the migration login's `VALID UNTIL` to a timestamp in
the past. The persisted URL therefore cannot connect in the deployment's
default state, even when manifest-only `EXECUTE` ACLs exist. A later reviewed
project must add a separate root-only, time-bounded activation and password
rotation mechanism; this milestone intentionally provides none.

## Existing installation preparation

Keep the live deployment pinned to schema `0006_worker_maintenance_health` and
`record_only`. Do not run this branch's migrations or operator profile during
Phase 2. When the later reviewed Phase 3 deployment gate explicitly authorizes
role preparation, create the root-only secret pair and provision roles with:

```sh
cd /opt/home/home-github/stack
secrets_root="$(sudo sed -n 's/^HOME_AGENT_SECRETS_DIR=//p' \
  /srv/home-agent/config/home-agent.env)"
case "$secrets_root" in /*) ;; *) echo "invalid configured secrets root" >&2; exit 1;; esac
sudo sh home-agent-deploy/add-identity-migration-role-secrets.sh "$secrets_root"
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm provision-roles
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm grant-runtime
```

The additive helper atomically publishes an independent password and database
URL, refuses every complete or partial legacy layout, compares the password
against every database role, and replaces the derived runtime directory with
exactly one mode-0400 `database_url` file. The migration container receives no
Core, BFF, operator, bootstrap, encryption, spool, or erasure credential and
can reach only the internal PostgreSQL network.

`home_agent_owner` receives a non-inherited, SET-only membership in the kernel
role solely so offline migrations can assign function ownership. No online
role receives that membership. Provisioning the roles or secrets does not
authorize a migration and does not advance rollout.

The database URL is a durable provisioned capability; Compose does not consume
or rotate it automatically. Its expired role is the current enforcement
boundary. After an authorized future operator run, rotate and re-expire its
password according to that future runbook.

The manifest kernel requires `SERIALIZABLE` transactions. A future activation
client must retry the complete transaction on SQLSTATE `40001` (serialization),
`40P01` (deadlock), or `55P03` (the kernel's five-second lock timeout). It must
never retry only a suffix of a manifest registration transaction.
