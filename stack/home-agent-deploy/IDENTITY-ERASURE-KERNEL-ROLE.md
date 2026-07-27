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

## Runtime restore boundary

The separate `home_agent_erasure` login is only a quarantined restore-replay
credential. Grant replay first removes every table and column ACL plus stale
owner-default, sequence, and function grants, then applies an exact
restore-replay allowlist. It has no table `DELETE`, no direct binding-table
mutation, and no direct People mutation. Existing identity cascades remain
behind the four reviewed owner-owned `SECURITY DEFINER` functions.

E2's person tombstone and normalized residual writes remain exclusive to the
offline owner. For deterministic suppression, the NOLOGIN kernel can read only
person identifiers from the block and principal tables plus a six-column,
forced-RLS projection of `knowledge.fact_versions`: `fact_version_id`,
`subject_type`, `subject_id`, `predicate`, `object`, and
`perspective_principal_id`. It has no table-level fact access, access to any
other fact column, residual, place, or initiative access, or table write
privilege.

The three base suppression helpers are callable only by the exact roles whose
RLS policies use them and by the kernel for its anti-resurrection trigger.
`identity_fact_version_is_visible(uuid)` is a separate principal-scoped boolean
helper executable only by `home_agent_api`, `home_agent_ingest`, and
`home_agent_erasure`, not to `PUBLIC` or the kernel itself. Its
`SECURITY DEFINER` body retains forced RLS and discloses neither fact contents
nor whether an invisible fact is missing, cross-principal, or blocked. Grant
replay verifies its SQL language, stable volatility, fixed settings, and exact
body digest before restoring those callers. A caller-side function quarantine
commits first, so a rejected same-owner replacement cannot retain earlier
execution grants. Trigger functions remain non-callable. The sole additional
restore capability is `EXECUTE` on the separately owner-owned
`replay_identity_person_retrieval_block_v2(jsonb)`. There is no direct
tombstone or residual DML for `home_agent_erasure` or any other online role.

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
gate below selects the E1 behavioral and lifecycle nodes separately, and also
exports the E2 owner and seven exact runtime-role URLs from mounted random
secret files inside its isolated client containers. A separate fifth cluster
receives the dormant E3 finalizer URLs from those files without activating the
expired login.

## Disposable PostgreSQL 17 admission gate

From a Windows or macOS disposable host with local Docker, run:

```sh
python3 tools/run-home-agent-e1-postgres-gate.py
```

Ordinary Linux execution is disabled before Docker discovery. The pinned
GitHub-hosted workflow is the only admitted Linux path: it passes the explicit
`--github-hosted-linux` flag and the exact GitHub-hosted runner context. A
self-hosted runner, bare local invocation, renamed host, or copied command is
refused.

`EngineeredLightingServer1` / `home-app` remains absolutely quarantined after
the unclean 2026-07-12 halt immediately following a high-churn E1 gate. The
runner checks both the process hostname and the Docker daemon name, so entering
a container with the host Docker socket does not bypass the named-host block.
There is no environment-variable bypass for either name quarantine. Removing
them requires a reviewed code change after a separate on-site
hardware/firmware stability review.

The operator command accepts no database or Docker target. The CI-only flag
does not select a target. The runner rejects endpoint-routing environment
variables and any active Docker endpoint other than local `unix://` or
`npipe://`, then pins that endpoint for every daemon command.

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

Behavioral, lifecycle, tamper admission, E2, and dormant E3 run in five sequential,
unexposed PostgreSQL 17 clusters on separate internal networks and tmpfs data
directories. The admission cluster keeps a locked revision-0007 template.
Every fresh upgrade path first stops at the live revision-0006a pin and replays
the current grant script before advancing, so production-pin compatibility is
tested rather than inferred. Each test case alone recreates `home_agent`,
provisions exact roles, upgrades
and grants through revision 0010, verifies that it is the sole database with
the three reviewed identity-kernel functions, then removes it. No test changes
function ownership to manufacture the production ownership invariant.

The fourth cluster upgrades and reapplies grants through revision 0012. It
runs the downgrade/refusal lifecycle contract first against its empty sole
`home_agent` database, verifies the sentinel/system-ID/database allowlist,
terminates only that labeled database's sessions, drops and recreates it, and
then reruns the full upgrade before schema, ledger, restore, runtime-role RLS,
anti-resurrection, and deployment contracts. It does not clone a second E2
database because revision 0011 deliberately admits the erasure-kernel ownership
set in only one database per cluster.

The fifth cluster repeats the guarded upgrade and grant sequence through
revision 0013, then runs only the E3 schema, database-kernel, write-fence, and
deployment contracts. Production remains pinned to 0006a, the finalizer login
remains expired, and no Compose service or live database is activated.

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
