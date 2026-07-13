# Identity erasure kernel role

Revision `0011_identity_erasure_e1` reserves
`home_agent_identity_erasure_kernel` for a later, narrowly scoped
`SECURITY DEFINER` erasure kernel.

The role is deliberately dormant in E1:

- `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS`, connection limit zero;
- no direct Agent Core database, schema, table, column, sequence, type,
  function, or owner-default ACL;
- no owned database objects;
- one membership only: `home_agent_owner` may `SET ROLE`, without `ADMIN` or
  inherited use;
- no erasure function or worker is installed.

The E1 tables are owned by `home_agent_owner`, use forced row-level security,
and expose only owner `SELECT`/`INSERT`. A later migration must add the exact
kernel policies and function privileges atomically with the audited function;
it must not grant a login role direct deletion authority.

## Schema boundary

- `privacy.person_erasure_scopes` distinguishes a confirmed, self-authorized
  whole-person request from the existing descriptor-only erasure workflow.
  Its scope commitment must equal the confirmation artifact's reviewed
  proposal digest, so an artifact cannot authorize different scope bytes.
- `privacy.subject_retrieval_blocks` is a dormant mandatory block marker and
  proves that the person came from the operation's confirmed manual scope. E1
  does not accept auto-expiry as authority; schedule hardening is a later gate.
- `operations.reviewed_identity_migration_erasure_receipts` is an idempotent,
  content-free completion record bound to one exact impact and the required
  retrieval block.

The tables contain UUIDs, category codes, counts, transaction/timestamp
metadata, and keyed commitments only. They do not retain names, aliases,
external identifiers, snapshots, source payloads, or before/after content.
The kernel quarantine covers the reserved media schema as well as every other
Agent Core application schema.

Manual scope also snapshots an authenticated HA-user binding and its confirmed
time, an immutable `ha_user` principal kind, and the confirmation consumed and
expiry times. A future active kernel must still revalidate that the principal
is active and the binding is not revoked in the same transaction. Ambient
PUBLIC schema privileges are not globally changed by E1; the kernel has no
direct application-schema ACL and every future function must retain a fixed
`pg_catalog, pg_temp` search path.

The retained authorization, audit, and block foreign keys mean this foundation
authorizes identity-migration leaf cleanup but does not promise physical
deletion of every semantic identity row. A future erasure kernel must scrub or
tombstone retained semantic rows unless a separately reviewed migration
redesigns those references.

Each E1 receipt is scoped to one reviewed migration run; its
`migration_run_erased` result is never a global person-erasure claim. E2 may
not activate a cleanup kernel unless the per-run closure proves at most 30,000
linkable leaf-commitment rows. Above that cap, it must retain the subject block
marker and record `closure_limit_exceeded` in a separately reviewed residual
schema; it must not relabel overflow as `linkage_unavailable` or success.

Production remains pinned below this dormant revision until the Phase 3
schema, restore, concurrency, privacy, and rollback gates pass.

The release gate must set `REQUIRE_PHASE3_IDENTITY_ERASURE_E1_TESTS=1` and
provide both disposable PostgreSQL URLs named by the E1 test module. In that
mode collection fails instead of silently skipping either the behavioral or
migration-lifecycle database test.
