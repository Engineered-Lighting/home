# Phase 3 activation — findings, 2026-08-25

The activation ran from step 12 to step 16 and is paused at step 17
(`commit_finalizer`) on a gap that cannot be closed by debugging. This records
what was found, what was fixed, and what is actually required to continue, so
none of it has to be rediscovered.

## Current state

- Database: `0015_current_authority_e5a` (was `0006a_worker_lease_arbitration`)
- Runner journal: `paused`, `next_step: commit_finalizer`, 16 of 35 steps banked
- Agent services: stopped, as the ceremony requires
- Accepted source pin: `09b4812`
- Backups: full backup `20260825-102555F` (10:25 UTC, ~7 h before the
  migration ran), checksum-verified off-host copy, restore drill passed the
  same day

## The blocker: nothing creates a reviewed identity migration run

`commit_finalizer` admits the signed finalizer document by inserting into
`operations.reviewed_identity_finalizer_admissions`. That insert is
`INSERT … SELECT … FROM migration_run`: it *copies provenance* out of a
pre-existing row in `operations.reviewed_identity_migration_runs` — release
manifest digest, migration tool bundle digest, core OCI/schema/capability
digests, policy digest, review signing key fingerprint. Without a matching run
row the statement inserts nothing, returns NULL, and the admission is rejected.

That table is empty. It is also new: it is created by revision 0007, which only
applied on 2026-08-25, so no run could have been recorded before tonight.

The tool that used to create runs is
`stack/home-agent-deploy/operator/migrate_legacy_identity.py`, which writes
through Core bootstrap endpoints. **Those endpoints are retired**
(`app/api.py`):

```python
LEGACY_IDENTITY_IMPORT_RETIRED = (
    "sequential legacy identity import is retired; use the reviewed atomic "
    "identity finalizer"
)
```

So the sequential import directs callers to the atomic finalizer, and the atomic
finalizer requires the output the sequential import used to produce.

A search of the whole repository finds `reviewed_identity_migration_runs`
written **only by tests**, in raw SQL. There is no production path — no API
endpoint, no kernel function, no operator tool — that creates one.

### What is actually required

A decision about how a reviewed identity migration run is created under the
atomic-finalizer design — what provenance it must carry, who attests it, how it
is reviewed — and then an implementation. This is design work that the plan
assumed and never specified. It is not a configuration change and cannot be
inferred from the existing code.

## Secondary finding: the Core readiness pin

Even once a run exists, Core cannot run against the migrated database.
`app/config.py` pins `readiness_migration = "0006a_worker_lease_arbitration"`
and `app/api.py` pins `PHASE3_SCHEMA_REVISION` to the same value; Core fails
closed at any other revision. Revision 0007's docstring states this is
deliberate, pending a "later atomic finalizer and authoritative-readiness
release" that does not exist on `main` or on
`codex/home-agent-phase3-authority` (both checked; both pin 0006a).

Measured against the live database at 0015, Core's model layer conforms well:
of the 58 tables `app.schema` models, **53 exist with no column mismatches**.
The 5 absent tables are all `parent_relationship_*`, created by revisions
0018–0021, which the ceremony has not reached. `app/api.py` already carries
`PRINCIPAL_BINDING_ADAPTER_REVISION = "0017_authenticated_binding_e5c"` and
`PARENT_RELATIONSHIP_ADAPTER_REVISION = "0021_parent_status_e5h"`, so the
application code anticipates the later revisions.

Advancing the pin therefore looks small, but it does **not** unblock the
activation on its own, and should not be attempted before the run-creation gap
is designed.

Do not force it with `HOME_AGENT_READINESS_MIGRATION`: that defeats the
fail-closed guard and runs Core against a schema it was never released to
handle.

Note also that Core hardcodes its database identity — the URL validators require
`/home_agent` exactly, plus specific roles, host and port — so Core cannot be
exercised against a differently-named probe database. Proving Core boots at a
later revision requires an isolated cluster, which is what the hosted E1 gate
builds.

## Defects found and fixed

All five sat in code paths that had never executed, and each surfaced as the
same generic message.

| # | Defect | Fix |
|---|---|---|
| 1 | Revision `0007` rendered its table DDL from `app.schema`, which had drifted to the post-0010 shape, emitting a foreign key to a table `0010` does not create until three revisions later. The chain could not apply at all. | PR #52 — DDL frozen in the revision |
| 2 | Revision `0011` hashed raw ACL text. PostgreSQL ACL arrays preserve grant order, so a database whose grants accumulated over time could never match a freshly provisioned one holding identical privileges. No single pinned digest could satisfy both CI and production. | PR #54 — ACL entries sorted before hashing |
| 3 | `/srv/home-agent/secrets/runtime/provision-identity-cutover-roles/` did not exist, while all 18 sibling directories did. | Created: `postgres_owner_password` (sibling value) and `postgres_identity_cutover_password` (fresh) |
| 4 | `tmpfs: [/tmp:size=16m,mode=1777]` is an unquoted YAML flow sequence; the comma split it into two entries and Docker rejected `mode=1777` as a mount path. Two provisioning services were unstartable. | PR #56 — entries quoted |
| 5 | `/srv/home-agent/secrets/runtime/identity-cutover/database_url` was missing. | Created (dir `0700` root, file `0400` uid 10001) |

## The diagnostics problem

Every defect above surfaced as the same sentence, with the underlying error
discarded:

- `phase3_migration_executor._run` — `stderr=subprocess.DEVNULL`
- `phase3_activation_runner._run` — `stderr=subprocess.DEVNULL`
- `phase3_authority_admission._invoke` — `stderr=subprocess.DEVNULL`

At one point the real cause sat four layers below the reported message. The
executor's `_run` also raises the same error for a non-zero exit, for stdout
over `MAX_OUTPUT_BYTES` (64 KiB), and for a NUL byte in stdout — three distinct
conditions an operator cannot tell apart.

Failing closed and reporting nothing are separable properties. Preserving stderr
on these paths would weaken no guard, and would have turned most of these
investigations from an hour into minutes. This is the highest-value change
available to this tooling.

## Operational notes worth keeping

- A `TEMPLATE` copy does not carry database-level ACLs (`datacl` comes back
  `NULL`) and has a different database name. Anything reading
  `has_database_privilege(...)`, hashing `datacl`, or naming the database
  behaves differently on a copy. Copies are faithful for pure DDL only.
- The cutover login is deliberately expired (`VALID UNTIL 1970-01-01`) as a
  disposable credential. Verify its password against the stored SCRAM-SHA-256
  verifier rather than by connecting.
- The host checkout's fetch refspec is restricted to
  `codex/home-agent-greenfield`, so `git fetch origin` silently does not update
  the integration branch and `merge --ff-only` reports "Already up to date" at
  the old commit. Fetch the branch explicitly.
- `core-api` mounts neither `./home-agent-deploy` nor
  `/srv/home-agent/private/phase3-identity`, so the documented instruction to
  run the import tool "inside `core-api`" cannot be followed as configured.
  Prefer copying into its `/tmp` tmpfs over adding bind mounts: a mount would
  expose household identity data to a long-running, network-facing service for
  its entire lifetime.
- There is no production restore procedure — only isolated drills
  (`isolated_restore_drill.sh`, `monthly_restore_drill.sh`), which explicitly do
  not touch the production cluster. Rolling production back currently means
  improvising a `pgbackrest` restore. A reviewed production restore script is a
  real gap worth closing before it is needed.
