# Home relational context contracts, first local shadow integration

These contracts are new versions alongside `home-context-1`. They do not reinterpret historical contexts, fitted probabilities, registry approvals, or the existing request/allocation v1 contracts. Every schema is pinned in `schemas/DIGESTS.json`; `node tools/build-home-context-service-schemas.cjs` reproducibly updates the new schemas and Apartment v2 pins together. Runtime semantic checks additionally enforce causal intervals, exact scope, original-root bindings and matching registration.

## Agreed boundaries

Inference supplies facts, anonymous associations, uncertainty, competing hypotheses, source health and geometry qualifications. The JavaScript Home engagement reducer owns observation progression and engagement/refinement dwell once per room. The lighting mapper owns desired outcomes, preferences, clarification proposals, holds and registered target selection. The existing allocator and virtual executor retain fixture and motion responsibilities.

`home-engagement-state-1` is deterministic support, with no fabricated probability. The old probabilistic Home contract remains unchanged. `apartment-lighting-request-2` carries full epoch-qualified beneficiaries and `relation_state_refs`. The explicit allocation adapter projects beneficiaries into opaque full-identity tokens for the strict v1 allocator, then publishes `apartment-lighting-allocation-2` with the original beneficiary/reference bindings and request digest. This is a computational adapter; a v2 payload is never presented as v1 without conversion.

## Scope, clocks and provenance

All semantic scopes use `site_id`, `room_id`, `session_id`, `domain_epoch`. A `person_ref` additionally requires `track_epoch` and anonymous `pid`. PID reuse, capture restart and continuity loss require a new track epoch. Person IDs do not identify an owner or inherit an owner's preferences.

Times are finite UTC epoch seconds. A fact separately retains `observed_at`, producer `available_at`, and half-open `valid_until`. Eligibility uses the latest of producer availability, journal commit and recorded consumer receipt. The journal publication does not rewrite producer times. Heartbeats, retry, historical summaries and reconnect never refresh an observation.

`sensor_roots` name the physical sources; `observation_roots` name original frames or device observations. An original root cannot change its physical binding or time across detector, pose, depth, Qwen, V-JEPA or Frigate derivatives. Multi-source facts require `sensor_root_bindings: {observation_root: [physical_root]}`; multi-frame facts require `observation_root_times: {observation_root: original_utc_seconds}`. Their union and maximum time must match the fact; a historical frame cannot be rebadged with a new observation time. Availability and interpretation TTL can differ between models.

Clock mappings must be commissioned. Original nanoseconds remain strings and device/session offsets need an explicit commissioned origin. Source composition must convert verified depth millimetres into metres before the separately granted geometry sidecar; the sidecar accepts registered metric geometry and performs no inferred unit conversion or transform. `instance_ref` is a within-frame binding, never a persistent PID. Bare Lab event-root fallbacks are rejected unless original media/source bindings resolve them.

## Registered semantic fields and grants

Schemas are the field allowlists. Registered fact predicates are closed: manipulation/interaction, working position/orientation, negative engagement, attributed PC input, proximity/pass-by, visible objects/active devices, person presence, material state/use, activity mode/proposal, conflict and qualified association. Model cues permit only registered posture, material, object and activity enums. New fields or kinds require an explicit contract revision, pin update, adapter and owner source grant. The semantic `value` field cannot carry images, embeddings, names, free-form model text or arbitrary coordinates.

Missing/stale/unobservable facts have null values. A supplied negative predicate is different. Failed acquisition produces a terminal reply, never invented disengagement. A camera spatial claim needs registered geometry revision, registration, coverage and person association. Spatial booleans cannot substitute for an actually verified registry. Region-only human declarations can select an approved fixed region without camera localization or a fabricated beneficiary. A sofa reading region does not locate a book.

Grants bind an authenticated principal to producer ID, room scope, operations, payload types, schema pins, revisions, source IDs, original sensor roots and (for intent writes) human actor. Geometry and decision allocation channels need explicit read permission. No default grant or bundled credential exists. Consumer admission feedback is runtime feedback; it does not establish correctness or evaluation truth.

Every grant pins exactly its allowed payload types. `clock_mappings` binds mapping ID to source, original unit, uncertainty bound and any commissioned relative origin. `registration_bindings` binds registration ID to source, geometry revision, registry digest and covered-region references; omission denies spatial publication. `expected_worker_id` is required for worker-commit grants. See [the service grant format](../../stack/services/home-context/README.md) for exact fields and hash computation. Model and legacy lineage metadata permit only registered identifiers, hashes and timing fields, never coordinates or free-form model text.

The worker commits `home-engagement-state-1` as a standalone semantic publication alongside its lighting decision in the same transaction. Another consumer can receive only that channel without an allocation/geometry read grant. Model hypotheses remain separately attributable proposals; they cannot seed the reducer or manufacture engagement.

## Wire protocols

The Python service and authenticated web gateway use `/api/home-context/v1`:

| Route | Meaning |
| --- | --- |
| `GET /snapshot?site_id=...&room_id=...` | Atomic retained state and cursor; no resumed dwell implied |
| `GET /events?site_id=...&room_id=...&after=...&stream_epoch=...&limit=128&wait_s=10` | Ordered cursor page; a missing retained cursor returns `reset_required` |
| `POST /events` | Internal producer publication, scoped separately from browser commands |
| `POST /evidence-requests`, `GET /evidence-requests/{request_id}` | Durable bounded acquisition and terminal lookup |
| `POST /evidence-replies` | Authorized explicit supported/abstained/unavailable/denied/expired/cancelled result |
| `POST /intent-events`, `GET /intent-events/{id}` | Separately attributed human declaration/correction and durable receipt lookup |
| `POST /consumer-feedback` | Input dispositions, reason codes and decision/evidence references |
| `GET /worker-commits/{worker_id}`, `POST /worker-commits` | Internal checkpoint/output transaction; requires worker authority |
| `POST /revocations` | Source/revision control; invalidates materialized state |

Commands acknowledge durable recording before returning. Retrying the same authenticated origin/epoch/ID and exact content returns the original receipt; changed content rejects. A timed-out request is looked up before another submission. Evidence requests identify person/region, existing evidence, missing distinction, permitted precision, age, deadline and budget; they cannot choose endpoints or create grants. Without a provisioned acquisition route, the service records an explicit unavailable reply. No models are called by this service.

Intent lookup adds compatible optional `application_result` metadata under the pinned `home-intent-application-result-1` schema. It records applied/rejected status, reason and the committed decision reference separately from the unchanged ingress receipt. The scoped intent read authorizes this command's status without an allocation read grant. The service stores that result atomically with the worker commit, so reconnecting after the one-time decision notification can still resolve the command. Absence means no terminal application was recorded.

Publication envelope:

```json
{"schema":"home-context-publication-1","id":"fact:demo:104","producer_id":"local-observer","stream_epoch":"journal-demo","cursor":104,"context_seq":104,"committed_at":1800000000.3,"payload_type":"fact-1","payload_version":"1.0","payload_digest":"<SHA256 of canonical payload>","payload":{"schema":"fact-1","version":1}}
```

The abbreviated payload above is an envelope illustration, not a valid complete fact. See the synthetic fixture and typed schemas for complete examples. Service error bodies use `home-context-error-1` and `error: {code,message}`. Journal cursor, context sequence and worker decision sequence remain separate.

Worker commits compare **both** `expected_input_cursor` and `expected_context_seq`; no new observations are needed for decision sequence to advance. The consumed cursor must have been delivered to that worker's principal. Checkpoint, output and sequence update atomically. An unconfirmed commit is retried byte-for-byte; it is never recomputed under the same ID. Revocation generation, config digest, schema versions and identity bind recovery.

Each decision also commits up to 128 `input_receipts` containing cursor, publication ID, payload digest and actual consumer receipt time. Snapshot/pages include the current room `revocation_generation` even when a semantic read grant hides the control payload. A generation change clears cached outputs and requires atomic snapshot recovery; it cannot refresh observation time or restore old dwell. Lost-ACK reconciliation also checks current authority before announcing a decision.

Offline evaluation adds `lighting-target-truth-2` with full epoch-qualified `person_ref`. Historical truth v1 stays readable, but cannot score an interval containing reused/ambiguous epochs under one PID. Independent labels, runtime intents and model proposals retain separate namespaces. The diagnostic evaluator excludes stages bypassed by the selected diagnostic and never counts oracle outputs toward sample floors.

## Explicit implementation refinements to the plan

Semantic messages remain 64 KiB and pages 1 MiB. A separately authorized internal worker commit has a 256 KiB cap because it carries checkpoint plus output atomically; it is not exposed through the browser gateway. The journal caps retained publications by age, bytes and count, plus a 64 MiB primary database limit. Immutable retry hashes remain durable; exhausting their bounded capacity requires explicit journal rotation. Retention is an upper bound, not a guarantee of 24-hour replay under every input rate. `wait_s` is a bounded polling hint in the initial single-writer service; empty pages may return immediately.

The initial reducer retains at most 1,024 immutable original-root bindings per epoch, and blocks new roots on exhaustion. It never evicts old root identity to make a demonstration pass. Use recording/session epochs for the bounded real-episode milestone; sustained unattended operation requires a separately validated capacity/retention design. These bounds are not continuous-production acceptance.
