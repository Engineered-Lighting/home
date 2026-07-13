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

E1 admission supports the dedicated Agent PostgreSQL cluster only. Its
non-system role inventory must be exactly the offline owner and the reviewed
`home_agent_*` roles provisioned by `provision-roles.sh`; an additional admin,
application, or shadow role is a hard refusal, not an inferred operator
exception. PostgreSQL-reserved `pg_*` roles must remain non-login and without
special role attributes. The only managed-role memberships are the reviewed
backup edge and the owner's three SET-only kernel edges.

The current database and the `pg_catalog`/`information_schema` namespace,
relation, live-column, routine, and type inventories—including their raw
ACLs—must match the canonical baseline from the pinned PostgreSQL 17 image,
revision 0010, and the reviewed deployment grants. This permits only the four
reviewed backup-function grants and rejects system-schema objects or grants
left by a tampered predecessor. Parameter privilege entries,
current-database role/GUC overrides, and offline-owner role defaults must be
empty. Subscriptions,
publications, publication
relations/namespaces, logical replication slots, and replication origins must
all be absent, and application relations must retain their default replica
identity.

The full Core regression gate must set
`REQUIRE_PHASE3_IDENTITY_ERASURE_E1_TESTS=1` and provide both disposable
PostgreSQL URLs named by the E1 test module. In that mode collection fails
instead of silently skipping either database test. The one-command adversarial
gate below selects the behavioral and lifecycle nodes separately because each
must run in its own production-shaped cluster.

## Disposable PostgreSQL 17 admission gate

From the repository root, run:

```sh
python3 tools/run-home-agent-e1-postgres-gate.py
```

The command accepts no database or Docker target. It rejects endpoint-routing
environment variables and any active Docker endpoint other than local
`unix://` or `npipe://`, then pins that endpoint for every daemon command.

It must run from a Git working tree. It generates a filtered build context
from two fail-closed sources: an exact reviewed file manifest (including the
new E1 tests before their first commit) and regular files enumerated by the Git
index beneath the reviewed Core trees. Untracked files not named by the exact
manifest abort the gate and are excluded. Git symlinks/special modes,
filesystem symlinks, sensitive path components or credential/key suffixes,
unreviewed file types, files over 2 MiB, NUL-containing content, and non-UTF-8
content also abort the gate. Unrelated private, `.git`, bootstrap, and change
data are never sent to the Docker daemon. Docker may retain cache derived from
that already-filtered context; the gate deliberately never prunes a machine's
global build cache.

Behavioral, lifecycle, and tamper admission run in three sequential,
unexposed PostgreSQL 17 clusters on separate internal networks and tmpfs data
directories. The admission cluster keeps a locked revision-0007 template.
Each test case alone recreates `home_agent`, provisions exact roles, upgrades
and grants through revision 0010, verifies that it is the sole database with
the three reviewed identity-kernel functions, then removes it. No test changes
function ownership to manufacture the production ownership invariant.

Before connection termination, database removal, role cleanup, or lifecycle
downgrade, the gate verifies a random 256-bit run sentinel, PostgreSQL system
identifier, local host contract, exact database allowlist, and exact cluster
inventory. Every Docker container (including transient clients), network, and
test image has the random run label and an allowlisted name. Cleanup discovers
resources by that label, inspects ownership, retries a bounded number of times,
and fails the gate if any residue remains. This verified cleanup runs after
success, ordinary failure, interruption, and SIGTERM. No process can guarantee
cleanup after SIGKILL, daemon/host loss, or power failure; the random labels
make any such residue unambiguous for operator inspection.

The gate never uses the production Compose project, volume, network, secrets,
or database URL.
