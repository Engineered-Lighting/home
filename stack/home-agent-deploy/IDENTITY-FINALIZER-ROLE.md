# Identity finalizer database roles

The deployment contains dormant groundwork for a future reviewed identity
finalization milestone. It does not implement or authorize finalization.

The groundwork uses a pair distinct from the revision-0008 manifest roles:

- `home_agent_identity_finalizer` is a `LOGIN`, `NOINHERIT`, `NOBYPASSRLS`,
  connection-limit-one role. Provisioning re-applies a 1970 `VALID UNTIL` on
  every replay, so the durable database URL cannot authenticate by default.
- `home_agent_identity_finalizer_kernel` is `NOLOGIN`, `NOINHERIT`,
  `NOBYPASSRLS`, and connection-limit-zero. Only `home_agent_owner` has a
  non-inherited, SET-only membership for possible ownership management by a
  later offline migration.

Neither role has application schema, table, sequence, function, type, default,
temporary-object, or database-creation authority. The login has database
`CONNECT` only, which remains unusable while it is expired. Grant replay
removes stale authority, including execution of the exact revision-0008
manifest functions. The roles receive no Core, BFF, operator, bootstrap,
service, bearer, encryption, spool, ledger, or media credential.

Revision 0009 adds normalized owner-only receipt-to-projection-target and
affected-person lineage tables. It adds no writer or finalization function,
grants no access to either finalizer role, and does not change this deployment
boundary. There is also no activation or password-rotation helper, semantic
projection, or finalizer client in this milestone. The
`identity-finalizer` Compose service is PostgreSQL-network-only and exits with
status 78 before opening its secret. Starting the operator profile therefore
cannot perform a review, mutate semantic authority, or advance rollout.

The adjacent offline E2 compatibility compiler does not change this role
boundary. It can authenticate a signed finalizer document and prove only that
its affected-person set is disjoint from the exact, short-lived,
ledger-attached tombstone rows supplied to that invocation. It cannot represent
an active pre-ledger block. It labels snapshot coverage and database currency
unproven, emits no deployable or commit-ready artifact, and has no database
client. An empty snapshot is never evidence that no tombstones exist. Only a
future atomic database kernel may lock and recheck complete E2 state while
committing semantic projections.

## Existing installation preparation

Production remains pinned to `0006a_worker_lease_arbitration` and
`record_only`. Do not run branch migrations or the finalizer service. After a
future reviewed deployment gate separately authorizes dormant role preparation,
an operator may atomically add its independent secret pair and replay only the
role and zero-authority grants:

```sh
cd /opt/home/home-github/stack
secrets_root="$(sudo sed -n 's/^HOME_AGENT_SECRETS_DIR=//p' \
  /srv/home-agent/config/home-agent.env)"
case "$secrets_root" in /*) ;; *) echo "invalid configured secrets root" >&2; exit 1;; esac
sudo sh home-agent-deploy/add-identity-finalizer-role-secrets.sh "$secrets_root"
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm provision-roles
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm grant-runtime
```

The additive helper refuses complete, partial, or legacy shared layouts;
compares the new password with every other database role; atomically publishes
the root-only master pair; and materializes exactly one mode-0400 database URL
for the fail-closed service. It prints no secret.

This is preparation only. Do not run `identity-finalizer`: no callable kernel
exists and its deliberate exit is a containment check, not an installation
error. A later project must review and use the normalized lineage foundation,
then introduce PostgreSQL 17 adversarial function tests, an atomic
`SERIALIZABLE` writer, and a separate root-controlled activation design before
any finalization can occur.
