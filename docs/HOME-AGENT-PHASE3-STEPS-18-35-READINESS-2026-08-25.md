# Phase 3 activation: readiness of steps 18–35

Status as of 2026-08-25. Written so the next session does not rediscover any of
this the expensive way.

**Summary: the activation cannot complete past step 22 with the code that
exists.** Three blockers are confirmed, one of them a production component that
was never written. Two of the three were verified against the live Home
Assistant host, not only by reading source.

This document deliberately records what is *not* broken as well, so that work
is not repeated.

## Why this matters more than an ordinary bug list

`PAUSE_STEPS` in `stack/home-agent-deploy/operator/phase3_activation_runner.py`
contains no step between 17 and 27. A single `advance` therefore runs
17 → 18 → 19 → 20 without stopping. That sequence registers the reviewed run —
irreversible, one-shot for the life of the database — then stops Home
Assistant, then fails.

Registration is one-shot because `rollout_authorizations` carries
`rollout_transition_once UNIQUE (from_mode, to_mode)`
(`alembic/versions/0004_rollout_authorizations.py`), the registration kernel
rejects any second run bearing the same authorization
(`0008_identity_migration_kernel.py`), and **no role holds `DELETE` on
`operations.reviewed_identity_migration_runs`**. Spending it on a run that
cannot reach the cutover leaves a full cluster restore as the only recovery,
and no production restore tooling exists — `isolated_restore_drill.sh` is a
drill harness.

**Do not run `advance` until blockers 1–3 are closed.**

## Blocker 1 — step 23 has no production writer for the E4 evidence

`CUTOVER_SQL` in `stack/services/home-agent-core/app/identity_admission_writer.py`
inserts `candidate_cutover_id` and `writer_freeze_id`, which carry hard foreign
keys to `operations.semantic_authority_cutovers` and
`operations.enforced_legacy_identity_writer_freezes`
(`alembic/versions/0014_identity_semantic_cutover_e4.py`).

Those identifiers are minted **offline** by the signing ceremony
(`phase3_semantic_cutover_packet.py`, `phase3_writer_freeze_evidence.py`) and
are never written to PostgreSQL. The whole application contains exactly two
`INSERT INTO operations.` statements — the finalizer admission and the cutover
admission, both in `identity_admission_writer.py`. Verified:

```
$ grep -rn "INSERT INTO operations\." stack/services/home-agent-core/app/
app/identity_admission_writer.py:121:  INSERT INTO operations.reviewed_identity_finalizer_admissions (
app/identity_admission_writer.py:195:  INSERT INTO operations.reviewed_identity_cutover_admissions (
```

The only code that populates the four evidence tables is
`stack/services/home-agent-core/tests/seed_phase3_identity_semantic_cutover_e4_success.py`,
a test fixture whose own docstring says it is "executed only by the labeled
GitHub-hosted PostgreSQL gate".

The offline producer and the database consumer have no bridge. Even with the
foreign keys satisfied, `operations.commit_reviewed_identity_cutover` separately
requires the candidate row, the writer freeze, the writer evidence, and exactly
six `privacy_cutover_check_receipts` rows.

**This is a missing component, not a patch.** But it is a well-shaped one, and
smaller than "write the E4 evidence" sounds. Everything it needs already
exists; only the bridge between the two halves is absent.

*It does not invent data.* The seeder derives its provenance **from the
database** — a join across the reviewed run, its finalization, its consumed
finalizer admission, and `operations.erasure_ledger_state` — and supplies only
the operator attestations as synthetic digests. That query is a usable
specification of the read side.

*The attestations already exist, signed.* `phase3_writer_freeze_evidence.py`
emits every field the writer-evidence table requires: `evidence_strength`,
`integrity_result`, `checkpoint_result`, `journal_result`,
`legacy_context_cutoff_status`, `freeze_kernel_build_digest`,
`evidence_commitment`, and `freeze_id`. The signing ceremony produces these as
private documents today (steps 21–23 sign them). Nothing carries them into
PostgreSQL.

*The grants are already in place.* All four tables appear in the owner-scoped
grant lists in `apply-grants.sh`, alongside the runs and admissions tables the
existing writers use.

So the component is a **third stdin-bridge writer** in the established shape of
`app/identity_admission_writer.py` and `app/identity_migration_registrar.py`:
one signed private document in on stdin, provenance read from the finalized
run, one governed write out. It still needs review, tests, and a hosted-gate
proof — but it is a known pattern applied a third time, not a new design.

### What the missing writer has to insert

Four tables, fed by three ceremony steps that already sign their documents. The
seeder inserts them in this order, and its column lists are the specification.

| Table | Fed by | Shape |
|---|---|---|
| `legacy_identity_writer_evidence` | step 21, `phase3_writer_freeze_evidence.py` | one row |
| `enforced_legacy_identity_writer_freezes` | step 21, same document | one row |
| `privacy_cutover_check_receipts` | step 22, `phase3_privacy_cutover_evidence.py` | **six** rows, one per check category — ingress, retrieval, prompt, initiative, export, edge-block |
| `semantic_authority_cutovers` | step 23, `phase3_semantic_cutover_packet.py` | one row, referencing the writer evidence and all six check ids |

The existing cutover admission and the `0014` commit kernel then consume the
last of these. Both already exist; only the four inserts above are missing.

The natural shape is one writer with three arms — freeze, privacy, cutover —
mirroring `identity_admission_writer.py`'s existing finalizer/cutover arms,
each taking the private document that step already signs. Note the ordering
constraint: `semantic_authority_cutovers` carries `writer_evidence_id` and the
six `*_check_id` columns, so it cannot be written before the other three.

## Blocker 2 — the five-minute evidence windows cannot be satisfied

`stack/home-agent-deploy/operator/phase3_privacy_cutover_observer.py` requires
both `freeze_time - edge_time <= 5 min` and `now - freeze_time <= 5 min`
(`MAX_EDGE_TO_FREEZE_AGE`).

`edge_time` is `refreshed_at` from
`/config/.storage/home_agent_edge_privacy_policy_receipt.json`, produced by
`ha-config/home_agent_edge/runtime.py` **only after a successful fetch against
`edge-ingress`/`core-ingest`** — services stopped since step 12, with the
unbounded human pause at step 17 in between. The gap is hours or days.

Both inputs are also create-once: `_freeze_legacy_writer` early-returns when the
observation file exists, and `_atomic_private` refuses to replace it with
different bytes. Once the window is missed it is missed permanently.

A sibling window at step 23 has the same shape:
`phase3_semantic_cutover_packet.py` requires the erasure-current receipt to be
≤ 5 minutes old, but it is produced at step 10 and no later handler refreshes
it. That one is at least recoverable out of band by re-running
`phase3_evidence_receipts.py erasure-current`.

## Blocker 3 — step 20, verified against the live HA host

Both of these fail **after** step 19 has already stopped Home Assistant.

**3a. `ha core info --raw-json` carries no run state at all.**
`ha-config/extended_openai_conversation/collect_legacy_identity_freeze_observation.py`
asserts `value.get("state") != "stopped"`. Live output on this deployment
(Core 2026.8.1) is the Supervisor envelope, and *neither* level has a `state`
key:

```
top-level keys: ['data', 'result']
data keys: ['arch', 'audio_input', 'audio_output', 'backups_exclude_database',
            'boot', 'duplicate_log_file', 'image', 'ip_address', 'machine',
            'port', 'ssl', 'update_available', 'version', 'version_latest',
            'watchdog']
```

So the comparison is `None != "stopped"`, which raises **unconditionally** —
including when Home Assistant genuinely is stopped. The fix needs a different
source of truth for "core is stopped", not a deeper key lookup. The only test
feeds a hand-written `{"state": "running"}` shape that this CLI never emits.

**3b. `/config/home-agent-operator` does not exist.** The same collector sets
`OPERATOR_ROOT = Path("/config/home-agent-operator")` and imports
`migrate_legacy_identity` from it to compute a plan digest. That string appears
**exactly once in the entire repository** — the constant itself. Nothing
deploys it, and `ls` on the live host confirms it is absent. The module lives on
the Ubuntu host at `stack/home-agent-deploy/operator/`, not on the HA host. The
collector's tests pass `operator_root` pointing at the Ubuntu-host path, so the
production default is never exercised.

## Verified present on the HA host — do not re-check

| Path | State |
|---|---|
| `/config/.storage/home_agent_edge_privacy_policy_receipt.json` | present |
| `/config/extended_openai_conversation/identity.db` | present, live (WAL active) |
| `/config/extended_openai_conversation/freeze_legacy_identity_semantics.py` | present |
| `/config/extended_openai_conversation/collect_legacy_identity_freeze_observation.py` | present |
| `/config/custom_components/extended_openai_conversation/identity_store.py` | present |
| `/config/custom_components/home_agent_edge` | present |

All four `REMOTE_*` constants the runner actually uses resolve. An earlier
audit note also listed `legacy_identity_fence.py` as required; it is **not**
referenced by any remote path constant, and its absence is not a blocker.

## Also verified sound — do not re-audit

- Signing-launcher arm names, and each arm's credential name set against the
  ceremony's own `CREDENTIAL_NAMES`.
- Compose service names and image entrypoint arms used by steps 25–33,
  including the three `phase3-migrate-*` revisions.
- Probe report keys read at steps 23/27/29/34 are all emitted by
  `app/phase3_activation_probe.py`, and each probe's pinned revision matches
  the database state at the step that calls it.
- Steps 26/33 starting Agent services in `shadow` mode: the rollout gate
  excludes `worker_maintenance_not_current`, and the phase-2 input digest is
  stable while ingest is stopped.
- Landing an operator-side change does **not** disturb the signing ceremony:
  `stage`/`review`/`finalize` have no coupling to `ACCEPTED_COMMIT`, the
  checkout, or the credential receipt, and `credential_source_binding_valid`
  is reached only from `_validate_live_prerequisites` at step 6.

## Lower-severity, still worth fixing

- **Permit staleness before step 25.** The activation permit has a 4-hour TTL,
  is armed at step 11, and is next re-armed at step 29 — with the unbounded
  step-17 pause in between. Recoverable out of band, but unsignposted.
- **`contain()` swallows a failed HA restart.** `except Exception: pass` around
  `_restart_ha` means a containment that fails to bring Home Assistant back is
  silent.

## Recommended order

1. Build the E4 evidence writer (blocker 1). Nothing past step 22 can be tested
   until it exists.
2. Re-shape the evidence windows (blocker 2) so a real ceremony can satisfy
   them — refresh the inputs inside the handlers, or relink the inequalities to
   the freeze rather than to wall-clock now.
3. Fix the stopped-core check and deploy `/config/home-agent-operator/`
   (blocker 3), and assert both in `_validate_pre_authorization_prerequisites`,
   which today checks only `python3 --version` and one file.
4. Re-arm the permit immediately before step 25.
5. Wire the registration kernel into the hosted gate: a disposable database at
   0013, a seeded shadow-predecessor row (which exists nowhere today), and a
   bounded activation window. It must **not** join the existing `e3` node list —
   the one-shot authorization collides with that phase's own fixture.
6. Rehearse `register → admit → finalize` on an isolated restored cluster
   before any live registration. Not a scratch database on the live cluster:
   `VALID UNTIL` lives in the shared `pg_authid` catalog and `pg_hba` does not
   pin the migration login to a database, so activating it would open it
   against the live database for the same window.
