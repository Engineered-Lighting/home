# Home Agent Core

Greenfield, model-independent authority for persistent home-agent state. It has
no import, database mount, or retrieval path from the legacy Intelligence
service.

## Trust boundaries

- PostgreSQL 17 is the only durable semantic/metadata authority.
- `runtime.sqlite` is a row-encrypted, 24-hour, 100 MB raw-observation spool.
  It must live on an encrypted local volume and must not be included in
  off-host backups.
- The BFF authenticates with `Authorization: Bearer <service-token>` and sends
  the HA user UUID in `X-Authenticated-HA-User`. The core resolves that UUID to
  a confirmed principal binding; it never accepts a client-supplied person or
  principal as authority.
- The HA Edge endpoint requires `Authorization: Bearer <edge-token>` in
  addition to mTLS. The private mTLS reverse proxy may inject this token; it
  must never accept the header from an untrusted network.
- Physical action, active-room perception, learning, and V-JEPA endpoints are
  intentionally disabled and return `capability_disabled`.

## Required configuration

Database and encryption secrets are required. Role credentials are minimized:
`api` requires separate service and operator-audience tokens, `ingest` requires
the edge token, and `worker` requires none. The bootstrap token is optional at
startup and should be mounted only into `api`; its bootstrap routes fail closed
when absent. The BFF receives only the service token, never the operator or
bootstrap credential.
The API role also accepts no runtime spool key and then opens no raw-observation
SQLite file; only ingest/worker roles require that key.
The worker role also accepts no durable knowledge key and does not construct the
semantic `CoreStore`; it only prunes the encrypted runtime spool and reports
health.

```text
HOME_AGENT_DATABASE_URL=postgresql+psycopg://...
HOME_AGENT_RUNTIME_SPOOL_PATH=/runtime/runtime.sqlite
HOME_AGENT_RUNTIME_SPOOL_KEY=<urlsafe-base64 32-byte key>
HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY=<different urlsafe-base64 32-byte key>
HOME_AGENT_EDGE_TOKEN=<random edge credential>
HOME_AGENT_SERVICE_TOKEN=<random BFF credential>
HOME_AGENT_OPERATOR_TOKEN=<different offline operator/migration credential>
HOME_AGENT_BOOTSTRAP_TOKEN=<different offline operator/migration credential>
HOME_AGENT_POLICY_VERSION=home-agent-mvp-v1
HOME_AGENT_POLICY_DIGEST=<64 lowercase hex characters>
HOME_AGENT_ROLE=api|ingest|worker|all
```

Production Compose should mount Docker secrets and set the corresponding
`_FILE` variables instead: `HOME_AGENT_DATABASE_URL_FILE`,
`HOME_AGENT_RUNTIME_SPOOL_KEY_FILE`,
`HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY_FILE`, `HOME_AGENT_EDGE_TOKEN_FILE`,
`HOME_AGENT_SERVICE_TOKEN_FILE`, `HOME_AGENT_OPERATOR_TOKEN_FILE`, and
`HOME_AGENT_BOOTSTRAP_TOKEN_FILE`. A
missing, unreadable, empty, whitespace-containing, or ambiguous direct+file
secret fails startup.

Use different spool, knowledge, edge, service, operator, and bootstrap secrets.
PostgreSQL must not publish a host port. Run the runtime role with a non-owner PostgreSQL account so
the migration's row-level policies apply. The migration owner is a separate
credential.

## Database

Run migrations explicitly as the migration owner:

```sh
docker run --rm --network home-agent-internal \
  -e HOME_AGENT_DATABASE_URL="$MIGRATION_DATABASE_URL" \
  -e HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration \
  home-agent-core migrate
```

The initial migration creates `ingest`, `identity`, `knowledge`, `engagement`,
`privacy`, `operations`, and reserved `media` schemas. Memory commits,
descriptor correction/retraction, initiative claims, parent confirmations, and
erasure use serializable transactions. Artifact lineage cycles are rejected in
PostgreSQL.

Production role separation uses the same image:

- `api`: typed semantic APIs; no ingest route.
- `ingest`: HA Edge ingress only.
- `worker`: runtime-spool retention, durable outbox processing, whole-person
  auto-expiry, and independent erasure-ledger receipts.
- `all`: local integration/testing only.

The worker registers a fenced random instance and immediately completes a
database-timed maintenance cycle before its own readiness can pass. The cycle
prunes the local spool, verifies the erasure-ledger head, and commits binding
retention plus its content-free success proof atomically. Its singleton state
is an unlogged PostgreSQL table: schema and constraints are durable, while a
crash or backup restore cannot resurrect an old success row as live authority.
API health degrades when the proof is stale; shadow/canary readiness and normal
mutations fail closed. Record-only ingest continues redacted delivery but does
not write sensitive raw spool content until maintenance recovers. Cancellation,
opt-out, forgetting, and erasure remain available.

Rollout mode is independent retention authority. Health reports precise
location retention and visit projection as disabled in record-only and shadow,
even when an enabled preference row survived rollback. Authenticated snapshots
still carry their typed subject and capability envelope, but non-canary reads do
not query or return visit metadata. Browser and native clients reduce that
response to the two stored preference booleans plus categorical rollout/opt-out
state, discarding the subject envelope. Preference opt-out remains enabled in
every mode, while opt-in is canary-only.

`/readyz` stays unavailable until the database reports migration
`0006a_worker_lease_arbitration`.

The production `migrate` entrypoint requires that exact revision through
`HOME_AGENT_EXPECTED_DB_REVISION` and rejects missing or different targets. It
does not upgrade to Alembic head because revisions 0007 through 0012 remain
dormant Phase 3 schema groundwork. It then verifies the database reports the
exact target; an old descendant database must be rebuilt rather than treated as
proof that the inserted hotfix ran.

A heartbeat timestamp in the future is intentionally still treated as fresh.
This fails closed during a host clock rollback instead of allowing two workers
to own the lease. If the owner is gone, keep workers stopped, correct and verify
the host clock, restart PostgreSQL so its postmaster-start fence changes, then
start exactly one worker. Do not delete or rewrite the lease row by hand.

Revision `0015_current_authority_e5a` is dormant database-only groundwork; it
is not the production migration target. Production remains pinned to
`0006a_worker_lease_arbitration` in `record_only`. The revision adds the
content-free categorical verifier
`operations.evaluate_current_identity_semantic_authority(uuid) RETURNS
varchar(32)`, with no API, BFF, UI, native, binding-write, or live-activation
surface.

Only a `home_agent_binding_operator` session may invoke the security-definer
function, whose current authority is the NOLOGIN
`home_agent_identity_authority_kernel`. Invocation requires a writable
`SERIALIZABLE` transaction outside recovery. Invalid UUIDv7 input, role, and
transaction boundaries fail respectively with
`identity_authority_status_input_invalid` (`22023`),
`identity_authority_status_role_invalid` (`42501`), and
`identity_authority_status_transaction_invalid` (`25000`).

The binding-operator role is capped at eight connections with 15-second
statement/idle-transaction, 5-second lock, and 30-second total-transaction
limits. E5a reapplies the same limits transaction-locally before taking the
semantic fence, run-row, and ledger locks, so a stranded pool transaction
cannot retain them indefinitely.

While holding the semantic fence, exact run row, and mutable ledger head, the
verifier revalidates one E4 promotion and its exact append-only E3/E4
finalization/admission, legacy writer-freeze, privacy, and projection graph.
It checks the database's monotone erasure-ledger continuity and the promoted
run's erasure impacts and subject retrieval blocks. It returns exactly one of:

- `current_database_authority`
- `promotion_missing`
- `promotion_invalid`
- `ledger_state_missing`
- `ledger_regressed`
- `ledger_diverged`
- `ledger_continuity_invalid`
- `erasure_impact_present`
- `subject_retrieval_blocked`

The positive category proves only current database authority relative to that
erasure overlay. It is not a fresh external encrypted-ledger check, Home
Assistant identity proof, current privacy-directive evaluation, or proof that
the external legacy writer is still frozen. It structurally rechecks the
immutable stored graph and exact E4 verifier-bundle marker; it does not
reverify the private E4 document or its signature. Future E5b must supply a
private authenticated HA confirmation and principal/person binding kernel,
invoke this verifier, and write the binding in the same `SERIALIZABLE`
transaction. Until then there is no semantic binding or activation capability.

## MVP API contract

Edge:

- `POST /v1/ingest/envelopes`
- `GET /v1/ingest/privacy-policy`

Core semantic API (the BFF exposes only its narrower explicit allowlist):

- `GET /v1/snapshot`
- `GET /v1/principal-binding-proposal`
- `POST /v1/principal-binding-request`
- `POST /v1/principal-binding-request/cancel`
- `POST /v1/principal-binding-proposal/confirm` (no-write
  `capability_disabled` tombstone until the atomic confirmation kernel exists)
- `POST /v1/source-entity-bindings` (no-write `capability_disabled` tombstone
  until database provenance constraints exist)
- `PUT /v1/preferences/{key}` (disable in every rollout; enable only in canary)
- `POST /v1/places`
- `POST /v1/visits`
- `POST /v1/memory-transactions` (typed Itaipava descriptor proposal)
- `GET /v1/memory-transactions/{id}`
- `POST /v1/memory-transactions/{id}/confirm`
- `POST /v1/facts/{id}/correction-preview`
- `POST /v1/descriptor-corrections/{transaction-id}/confirm`
- `POST /v1/facts/{id}/retraction-preview`
- `POST /v1/descriptor-retractions/{transaction-id}/confirm`
- `POST /v1/forget-preview`
- `POST /v1/facts/{id}/forget-preview`
- `POST /v1/erasure-requests/{id}/confirm`
- `GET /v1/erasure-requests/{id}`

The former per-item legacy People import endpoints remain authenticated
tombstones and always return `capability_disabled`. They never parse submitted
identity content or open a database transaction. Reviewed migration can proceed
only through the separate atomic finalizer after its rollout gates are met.

The offline operator-only `principal_binding_candidate_staging` compiler now
closes one source-provenance gap without enabling binding. It re-verifies the
raw identity-review bundle, distinct finalization envelope, and canonical
private source snapshot, then resolves one exact Person-projection receipt. An
offline operator must choose that receipt because the signed legacy data cannot
declare who is `me`; the choice itself is explicitly unauthenticated and
non-authoritative. The compiler accepts no name, alias, HA user, principal,
actor, or legacy-role selector. Its output keeps operator authority, current HA
identity and graph state, privacy/retrieval/erasure state, confirmation,
single-use consumption, binding creation, location memory, and travel greetings
false or unverified. It is `non_deployable`, `capability_disabled`,
`coverage_unproven`, and non-portable, and it is not imported by Core API/store,
BFF, native, browser, Edge, or Compose services. A future ceremony must reverify
the raw artifacts and prove the authenticated selector and all transaction-time
state; this Python result is not a binding candidate receipt.

There is no direct parent-confirmation API. Caller-supplied parent/child UUIDs
cannot create a single relationship edge, even with the Core service bearer.
The source-only `app.parent_confirmation` compiler can now render and verify
exactly two server-staged candidates as one private, digest-bound intent, but
its output is explicitly non-authoritative, not commit-ready, and
write-disabled under a dormant-only contract version, with status
`capability_disabled`. It is not imported by Core API/store, BFF, native, or
browser code. Explicit parent facts remain disabled until a later reviewed
schema and serializable commit kernel uses a new deployable contract version
and consumes a fresh authenticated confirmation without weakening those
boundaries. The dormant compiler binds the supplied server snapshot but does
not authenticate its receipt strings, mint or consume a confirmation artifact,
choose a fact valid-from time, or provide replay protection.

The offline operator-only `parent_confirmation_staging` compiler now closes one
narrower provenance gap before that preview: it re-verifies the canonical
identity-review bundle and distinct finalization signature, derives every
signed legacy `parent` label without accepting a parent selector, and requires
exactly two distinct People rows not archived in that signed snapshot. Its
current person/privacy/retrieval axes remain unverified, and its Python result
is not portable proof: any future consumer must re-verify the original signed
artifacts and reconstructed source rows. Its output is still
`non_deployable`, `capability_disabled`, and `coverage_unproven`; it does not
authenticate the current child binding, prove complete erasure/retrieval state,
mint a fresh gesture artifact, or create facts. It is not shipped or imported by
Core API/store, BFF, native, browser, Edge, or Compose services. The existing
dormant preview therefore still cannot be treated as a deployable consumer of
that result.

Operator-only identity review (never proxied through the BFF):

- `GET /v1/operator/principal-binding-requests`
- `POST /v1/operator/principal-binding-proposals`

Private native-only API (requires the BFF-created
`X-Home-Agent-Channel: private_tauri_attested_v1` and validated installation
UUID in addition to the service credential and authenticated HA UUID):

- `GET /v1/initiatives` (opaque eligible IDs and expiry only)
- `POST /v1/initiatives/{id}/claim` (the only response containing greeting text)
- `GET /v1/places/{id}/descriptor-relationship`
- `GET /v1/places/{id}/parents/current-presence`

The initiative routes return deterministic `capability_disabled`; their store
methods remain isolated future-domain evidence for synthetic tests. The
deployed BFF, Rust commands, and Agent UI also expose no list, claim, or
presentation capability. Re-admitting them requires a separate reviewed
initiative-capability and rollout gate.

Offline operator-only API (requires the operator bearer plus bootstrap
credential and is never proxied by the BFF):

- `GET /v1/operator-rollout`
- `GET /v1/operator-rollout/phase2-readiness`
- `GET /v1/operator-rollout/phase3-readiness`

The Phase 2 readiness response is read-only and deterministic. It reports the
seven-day window beginning at the first qualifying envelope and counts only accepted, identity-redacted
precise-location transition headers toward the 500-event threshold.
Conversation metadata, raw-retained location, snapshots, and gaps are reported
or ignored but never counted. Controlled journeys are never enumerated
automatically: the
operator must supply paired principal/visit UUID query parameters, after which
the inspector verifies explicit location consent predating the visit, a
departed sufficient visit, a continuous device-tracker root, ten-minute dwell,
and no overlapping snapshot or coverage gap. The response contains no names,
coordinates, locators, state payloads, or source event content. Journey results
are informational only in the v3 contract and never satisfy live readiness.
The v3 gate also requires the fenced worker-maintenance cycle to be current;
the response exposes only its categorical state, never the worker instance,
timestamps, retention counts, or queue content.

The Phase 3 endpoint is a fixed revision-`0006a_worker_lease_arbitration` gap
diagnostic, not a rollout gate. It rechecks the actual database revision and
reports only the configured rollout mode plus a categorical shadow-predecessor
status. Exact `shadow` mode and rollout-gate code `authorized` are required for
that predecessor status; a `record_only` code is never accepted as shadow
authorization. The response performs no identity or fact query, exposes no
counts, identifiers, names, timestamps, digests, evidence content, or private
records, and has literal `authoritative=false`, `enables_writes=false`, and
`ready_to_advance=false`. Query parameters and GET bodies are rejected.

The revision 0006a runtime line has no authoritative People migration
manifest/completion receipt, privacy-cutover attestation, legacy semantic-write
freeze attestation,
unique `me` marker, or staged atomic two-parent confirmation protocol. The
diagnostic therefore reports those fixed gaps as `unproven`, `not_evaluated`,
or `capability_disabled`. An authoritative Phase 3 v1 gate requires a future
reviewed migration and separate implementation; this endpoint cannot enable
semantic writes or be proxied by the BFF, Agent origin, browser, or native app.

When v3 readiness is true, place a JSON object containing a random
`operator_request_id` and the response's exact `expected_rule_version`,
`expected_policy_version`, `expected_policy_digest`, and
`expected_input_digest` on stdin for the operator-profile one-shot:

```sh
docker compose --env-file /srv/home-agent/config/home-agent.env \
  --profile operator run --rm -T rollout-authorize < /root/shadow-request.json
```

The one-shot receives a dedicated database credential that is never mounted in
Core API, ingest, worker, BFF, or the Agent origin. It first verifies migration,
restore-quarantine, and storage gates, then recomputes Phase 2 and snapshots
the current worker proof inside the same serializable receipt transaction
before storing one content-free `record_only` to `shadow` receipt. Runtime
worker freshness is still rechecked after promotion; a historical receipt
never asserts current liveness. Input is capped at 4 KiB, strict, duplicate-key rejecting,
and content-free; output is the content-free receipt. The online API role is
SELECT-only on receipts and has no authorization route.

Every Core role configured as shadow validates the receipt's policy, rule, and
immutable first-500-envelope evidence boundary before startup. Envelope rows
are append-only to the online ingest role and guarded against update/delete at
the database boundary; the separate erasure role retains governed deletion
authority. Canary requires a separate future receipt and a schema migration
that opens that transition. No rollout operation changes deployment
configuration, and no authorization route is present in Core API, BFF, or
native allowlists.

The proposal route accepts only the typed `place_social_descriptor` MVP shape;
there is no generic predicate/object write API.

Descriptor correction and retraction are also typed, authenticated
preview/confirm flows. A confirmation must repeat the exact digest of the
reviewed preview and provide a distinct confirmation artifact. Correction
closes the prior `system_range`, adds the next fact version, and may replace the
encrypted perspective-scoped wording; place, locator, visits, and parent facts
are immutable in this transaction. Retraction closes the accepted version and
adds a current `suppressed` version without scrubbing governed content. Both
invalidate only artifacts and pending initiatives explicitly linked to the
superseded descriptor version. Concurrent previews are bound to their exact
base version, so only one can commit.

Forgetting remains a separate privacy operation. It immediately blocks
retrieval, scrubs every descriptor version and encrypted wording transaction,
suppresses explicitly descriptor-derived initiatives, and invalidates derived
views while preserving the place, locator, visits, locality, and independent
parent facts.

Initiative listing returns no location wording, so two desktop clients cannot
both present a pending greeting. Claim is a serializable one-time transition;
it revalidates both current consents, a <=15-minute specific conflict-free
visit, sufficient coverage, the encrypted locator match, and the active
explicitly confirmed descriptor before returning the deterministic greeting.
Principal browser snapshots expose neither initiative records nor source-global
coverage-gap entity/timestamp details.

Descriptor explanations decrypt only the active perspective-scoped wording and
resolve current visible parent facts to reviewed display names. The independent
presence query does not treat that descriptor, the parent facts, or the
principal's phone as presence evidence; without fresh sufficient evidence from
at least two distinct roots and dependency domains it returns `unknown`. A
privacy-hidden or non-active confirmed parent keeps the role expansion and its
aggregate presence answer unresolved without exposing the hidden identity or
count.

An insufficient property anchor keeps its teaching transaction in
`needs_confirmation` with `location_unresolved`. The preview includes the exact
encrypted-locator retention summary covered by its digest, but confirmation is
blocked until the client obtains precise evidence and creates a fresh preview.

People creation, direct place/visit creation, and legacy imports are not BFF
routes. They require the appropriate reviewed offline operator/migration
workflow. Source-entity binding is disabled rather than delegated to an unsafe
offline shortcut. Direct principal binding no longer exists:
authenticated subjects can request, inspect, and cancel through exact BFF
routes; confirmation is a no-body/no-store tombstone. Proposal staging uses the separate
`home_agent_binding_operator` database role and is never BFF-routed. Neither a
BFF credential nor an arbitrary HA UUID can bootstrap identity authority.

`home_agent_api` is reset after every grant replay to no identity-table
privileges, then receives SELECT on only the eight tables used by current
authentication, binding, and privacy checks. Its direct identity writes are
column-limited to principal-binding request creation/closure and proposal
cancellation/expiry, plus immutable principal-scoped confirmation receipts
needed by privacy opt-out and descriptor lifecycle workflows. It has no
principal, HA-binding, People/alias/recognition/privacy/legacy-candidate, or
source-entity-binding INSERT; no future-table defaults; and no arbitrary-person
binding-cancellation function. This exact-table SELECT list is still a service boundary: several
readable legacy identity tables do not yet provide reviewed per-principal RLS,
so broader semantic retrieval remains disabled.

The source-entity route is intentionally a no-body, no-store tombstone. The
current table cannot enforce the principal/person pair, self-confirmation, or
confirmation-artifact provenance at the database boundary; record-only ingest
does not require it, and Frigate enrollment remains outside this API.

Only in canary, once a reviewed `device_tracker.*` binding, encrypted locality,
and the two separate opt-ins exist, the ingest role projects a visit from two
distinct-root, <=50 m fixes over at least ten minutes. It ignores copied
`person.*` projections, snapshot-only coverage, tracker switches, stale fixes,
overlapping localities, and locations outside reviewed private localities. Only
localities explicitly marked `travel_greeting_eligible` can create the single
private Tauri initiative.

Outside canary—or before that tracker binding and `location_memory` opt-in—
precise person, tracker, zone, and snapshot payloads are never written to
`runtime.sqlite`. A stale enabled preference cannot override rollout mode.
`do_not_track` and `ignored` directives apply the same fail-closed gate even
after opt-in. PostgreSQL retains only a sequence/clock header with identity,
HA context, source lineage, and low-entropy plain digests removed; its payload
fingerprint is keyed. After a locality match, future visits decrypt only that
principal's active child-property locators. Exactly one in-radius property may
win; zero or multiple matches remain at locality resolution.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check app tests
```

The focused suite covers encryption purpose binding, spool plaintext leakage,
idempotency/expiry/overflow gap markers, UUIDv7, GPS dwell/jitter/source
conflicts, evidence-root duplication, descriptor authority/consent/ambiguity,
prohibited implications, correction/retraction preview tampering and
concurrency, scoped forgetting, and the PostgreSQL schema privacy contract.
