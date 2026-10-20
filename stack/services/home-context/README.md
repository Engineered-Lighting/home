# Home context local shadow service

This stdlib-only service journals typed local facts, declarations, evidence requests, replies, consumer feedback, and worker output. It runs only on a literal loopback address. It contains no acquisition, host contact, model calls, device writes, calibration fitting, or production acceptance.

The worker atomically publishes standalone `home-engagement-state-1` for semantic consumers alongside the complete lighting decision. A semantic-only read grant does not expose allocation or geometry. `home-relation-hypothesis-1` is a separately granted diagnostic source proposal; the worker does not admit it as fact or engagement authority.

Run from this directory with an explicitly prepared grant configuration and a new operator-owned database path:

```text
python -B server.py --database <local-journal.db> --grants <explicit-grants.json> --host 127.0.0.1 --port 8768
```

The parent directory must already exist. No grant file or credential is bundled. The grant configuration is `{"schema":"home-context-grants-1","version":1,"grants":[...]}`. Each grant requires:

- `principal_id`, `producer_id`, and `token_sha256`: the SHA-256 of the explicit bearer credential, never a default token.
- `scopes`: exact `{site_id,room_id}` pairs. No wildcard site or room is accepted.
- `operations`: an explicit subset of `read`, `event`, `intent`, `feedback`, `evidence_request`, `evidence_reply`, `worker_commit`, `revoke`.
- `payload_types` and `revisions`: exact read and write allowlists. Hidden types and revisions never appear in snapshots, pages, or command lookups. Geometry requires its own explicit `home-geometry-1` grant; complete shadow decisions require an explicit decision-channel grant.
- `schema_pins`: an exact map from every allowed payload type to its pinned schema digest. `schema_registry.schema_digest(type)` computes the pin. Standalone schemas use SHA-256 of LF-normalized bytes. `home-context-1` uses SHA-256 of canonical `{repository_relative_path:SHA256_LF_bytes}` for its root and `common.schema.json` dependency. Unknown types, missing pins, changed bytes and unregistered schema references fail before opening the database.
- `source_ids` and `sensor_roots`: explicit logical-source and physical-root allowlists for fact writes; omitted lists admit no facts. Source health and coverage require their granted logical source.
- `clock_mappings`: `mapping_id -> {source_id,original_unit,max_uncertainty_s,epoch_origin_s?,source_epoch?}`. Facts and geometry require a matching commissioned source/unit and bounded uncertainty, plus original clock value mapping to `observed_at` within one microsecond of numeric representation. Relative units require an explicit commissioned epoch origin and source epoch. Omission denies facts and geometry.
- `registration_bindings`: `registration_ref -> {source_id,geometry_revision,registry_digest,coverage_refs[]}`. Any fact with spatial lineage, or geometry publication, requires an exact source/revision/digest binding and nonempty coverage refs within that commissioned allowlist. Omission denies spatial publication. A geometry publication's logical source is its authenticated `producer_id`; that name establishes no physical independence.
- `expected_actor_ref`: required for intent writes, bound to the configured human declaration actor.
- `expected_worker_id`: required for `worker_commit` authority; commit and checkpoint identities must match this configured principal.

For each `(site_id,room_id)` with worker writes enabled, grant configuration admits one `(principal_id,producer_id,expected_worker_id)` authority. Overlapping tokens may rotate for that identical authority; a different principal, producer, or worker in the same room rejects configuration before SQLite opens. Rooms with no worker grant remain read-only or ingress-only. Shared `home-engagement-state-1` and `home-lighting-shadow-decision-1` writes require `/worker-commits`; an `event` grant cannot publish them through `/events` or `/publications`. The existing `home-context-1` compatibility channel retains its granted producer ingress.

`/health` is an unauthenticated capability-free health record. Every other route requires `Authorization: Bearer <configured credential>`. Read access requires its own grant. Authentication failures and scope failures return typed error records without echoing payloads or credentials. Default HTTP access logging is disabled.

## API

All routes except health start with `/api/home-context/v1`:

| Route | Body / response |
|---|---|
| `GET /snapshot?site_id=...&room_id=...&consumer_id=...` | Atomic materialized latest records and tail cursor. `{schema:home-context-snapshot-1,stream_epoch,cursor,items,revocation_generation}`; items sorted by cursor. |
| `GET /events` or `/publications` | Same scope query, plus `after`, `stream_epoch`, `limit` (1–128), `wait_s` (0–10). `{schema:home-context-page-1,stream_epoch,after,items,next_cursor,revocation_generation}`. `wait_s` is a maximum; this bounded single-threaded implementation returns the current page immediately. |
| `POST /events` or `/publications` | Exact `home-context-event-1` producer envelope. |
| `POST /evidence-requests` | Raw `home-evidence-request-1` version 1. Accepted requests are durable `pending` ingress records. |
| `GET /evidence-requests/<request_id>` | Scope query; `{request,receipt,terminal_reply}`. |
| `POST /evidence-replies` | Raw `home-evidence-reply-1` version 1; one immutable terminal reply per request. |
| `POST /intent-events` | Raw `home-intent-event-1` version 1; the service binds producer identity from the grant and checks actor identity. |
| `GET /intent-events/<id>` | Scope query; `{event,receipt}`. `accepted` means durable ingress. Application acknowledgement comes from shadow decision `intent_results` (`applied` or `rejected`); `input_results` describes input admission. |
| `POST /consumer-feedback` | Raw `home-context-consumer-feedback-1` version 1; runtime feedback is never a human gold label. |
| `POST /worker-commits` | Atomic typed commit of input cursor, checkpoint, and output publications. |
| `GET /worker-commits/<worker_id>` | Scope query; `home-context-worker-checkpoint-1`, input cursor, context sequence, checkpoint, journal epoch, revocation generation. |
| `POST /revocations` | Producer envelope containing `home-context-revocation-1`; invalidates matching state and derived shadow context, journals the control event, increments room revocation generation. |

`site_id` and `room_id` are required on scoped reads. A page may advance `next_cursor` across records outside the caller's room or type/revision grants when no visible records remain. At the page or byte limit it stops exactly at the last emitted record. No visible publication is skipped. Delivered cursors also advance on empty pages. A retained-cursor miss, future cursor, or journal-epoch mismatch returns `409 reset_required` with the current journal epoch, tail cursor, and `retained_after`. Recover with one atomic snapshot before consuming subsequent events. Checkpoint reads require both `read` and `worker_commit` authority.

Snapshot and page metadata always carries the room's atomic `revocation_generation`, including when the control payload is hidden by a type grant. A changed generation requires clearing prior derived output and obtaining a fresh snapshot. This reveals no revoked source details and does not widen payload access.

## Envelopes and transactions

The producer event has exactly these fields:

```text
schema: home-context-event-1
schema_version: "1.0"
id, producer_id, stream_epoch
site_id, room_id, session_id, domain_epoch
observed_at, available_at, valid_until  # Unix epoch seconds
payload_type, payload_version: "1.0", revision, payload
```

Typed new payloads use integer `version: 1`; the existing `home-context-1` words contract uses its `schema_version`. Fact and geometry timestamps must equal their enclosing source-event timestamps. Availability must be causal; admission never refreshes source observation time or expiry. A fact requires a granted commissioned clock mapping, physical sensor roots, original observation roots, and full scoped person identity or explicit unresolved association. Closed fact predicates and value enums exclude coordinates or free text in semantic values. A persistent person reference is `{site_id,room_id,session_id,domain_epoch,track_epoch,pid}`. An observation's `instance_ref` cannot supply it.

Optional `model` metadata contains only registered model/config/calibration/run identifiers and digests. Optional `legacy_lineage` contains registered dialect/arm/capture identifiers, generation, map identifiers/digest, rule and pairing skew. Coordinates and arbitrary source objects cannot be hidden in these semantic metadata fields. Supplied Lab arm/generation/capture lineage is preserved; absent lineage is not invented.

Multiple original observations require exact `observation_root_times:{observation_root:original_epoch_seconds}` with the maximum equal to the fact's `observed_at`; optional `observation_window.end` must equal that same capture time. Multiple physical roots require exact `sensor_root_bindings:{observation_root:[original_physical_roots]}` covering every observation and exactly the physical-root union. Original physical ancestry and per-root capture time are immutable across new model/event IDs. Model-specific expiry may differ. Neither a new model ID nor a later wrapper timestamp creates a new observation.

Publications have exactly `{schema:home-context-publication-1,id,producer_id,stream_epoch,cursor,context_seq,committed_at,payload_type,payload_version,payload,payload_digest}`. The publication/page epoch is the durable journal epoch; producer epochs remain in stored ingress envelopes and their retry identity. Transport identity is journal epoch plus cursor; publication IDs are producer-local and may repeat across publishers. A retry of the same producer, producer epoch, and ID returns the original receipt only when the canonical complete event matches. A changed payload returns `409 identity_conflict`. Identity records are retained even after publication retention expires; the service fails admission at the configured identity budget rather than forgetting retries.

Canonical JSON uses UTF-8, sorted UTF-16 object keys, JSON number normalization (`1.0` → `1`, `-0` → `0`, JSON.stringify exponent thresholds), no whitespace, and SHA-256. The deterministic suite checks Unicode, numeric thresholds, and digest equality against the shared JS contract. Large integers outside the JavaScript safe integer range are rejected; original camera nanoseconds are retained as strings inside the clock record.

The exact worker commit envelope is:

```text
schema: home-context-worker-commit-1
schema_version: "1.0"
id, worker_id, producer_id, stream_epoch, site_id, room_id
expected_input_cursor, expected_context_seq, input_cursor
input_receipts: [{cursor,id,payload_digest,received_at}]  # at most 128
checkpoint: {config_digest, domain_epoch, revocation_generation, state:{seq,...}, ...}
publications: [producer event envelopes]
```

Workers must first read with `consumer_id=worker_id`. A commit cannot consume beyond the journal tail or the cursor actually delivered to that principal and consumer. Expected input cursor and independent context-sequence comparison prevent concurrent checkpoint overwrite even when a tick consumes no new input. Checkpoint `state.seq` and decision `seq` must equal the next context sequence. The checkpoint must acknowledge the current room revocation generation. Input receipts must match durable, delivered, scoped event cursor/ID/digest and causal service commit time; `received_at` has a 50 ms numeric/clock tolerance into the future and supplies no new fact authority. Decision input-receipt metadata must exactly match its commit. Outputs, retry receipt, context sequence, and checkpoint commit in one SQLite transaction. A failure rolls all of them back. Retrying the identical commit returns the prior commit receipt; a new commit cannot reuse an older output identity. Shadow lighting decisions explicitly carry `actuated:false` and `acceptance_eligible:false`.

Intent lookup retains the immutable original `receipt.status:"accepted"` and digest. After the worker settles that intent, the compatible optional wrapper field is `application_result:{schema:"home-intent-application-result-1",version:1,id,status:"applied"|"rejected",reason,decision_ref}`. It is receipt metadata accessed through the existing scoped `home-intent-event-1` read grant; it is not an event-writable payload type and exposes no allocation. The first result persists atomically with the decision, output and checkpoint, even when a browser misses the decision publication or reconnects to a newer snapshot. A result must identify the enclosing decision and one prior durable intent in that decision's full site/room/session/domain scope, within the worker's consumed cursor, available and service-committed by the decision time. Invented, ambiguous and unconsumed IDs reject the whole transaction. Repeated identical outcomes preserve the first decision reference; a later different status or reason returns `409 application_result_conflict`. Storage is one compact result per existing intent receipt, within the existing receipt/database capacity. Original bodies may still expire at retention and lookup then returns `410 artifact_expired`; no body or outcome is reconstructed.

Evidence requests use `request_id`, `correlation_id`, `consumer_id`, full scope, optional `person_ref`, `surface_id`, `region_id`, `geometry_revision`, `existing_evidence_refs`, closed `distinction` and `permitted_precision`, `max_observation_age_s`, `deadline`, and bounded `{frames,model_calls,wall_s}` budget. There is one active request per full person/region/distinction. The shipped service has no acquisition router: each accepted pending request immediately receives a separate durable `unavailable/source_not_provisioned` terminal publication. The constructor's non-null router marker is a composition/test scaffold for externally fulfilled pending requests; no dispatch handler is implemented. Caller URLs and module names are never accepted. Pending requests receive durable `expired/deadline_elapsed` replies when scoped reads perform maintenance. There is no hidden acquisition retry.

Replies use `terminal_status` in `supported`, `abstained`, `unavailable`, `denied`, `expired`, `cancelled`; full scope, `available_at`, matching request and correlation IDs, `reason`, `evidence_refs`, and `alternatives`. A supported reply needs uniquely scoped, non-revoked, current existing supplied `fact-1` records within the request's observation-age budget and matching requested person when supplied. It creates no evidence and does not certify the inference's truth. Terminal publication IDs are `evidence-reply:<sha256(request_id)>` to remain bounded for long request IDs.

## Resource bounds and verification

Defaults: 64 KiB semantic messages, 1 MiB responses, 128/512 KiB materialized state items, a 4,096-row/16 MiB/24-hour publication ring (first bound wins), 262,144 durable identities, 64 active requests, 32 workers, 128 consumer cursors, 64 MiB SQLite primary database, 2 MiB SQLite cache, and JSON nesting depth 24. Versioned `worker_commit` authority has a separate 256 KiB combined checkpoint/outputs envelope limit; each semantic publication remains within 64 KiB. SQLite uses DELETE rollback journaling, FULL synchronization, in-memory temporary storage, and an enforced primary page count. Budget temporary disk headroom up to another primary-database-sized rollback journal. The server admits one HTTP request at a time, queues eight, and times out connections after three seconds.

The ring evicts old publications and advances the retained-cursor floor; readers must reset after eviction. Large expired source bodies compact into immutable hash/receipt tombstones; worker commits and decision identities store tombstones immediately so checkpoints and decisions are not copied into every receipt. Active source bodies remain available for supported replies. Durable receipt/root-binding metadata is never silently forgotten. Its cap or primary-database limit can be reached before 24 hours for some event rates and payloads; admission then fails atomically and an owner-planned new journal epoch is required. The 24-hour value is a retention ceiling, not verified sustained capacity. No automatic rotation or 24-hour production qualification exists. Restart retains epochs, retries, checkpoints, root lineage and revocations.

Tests create only synthetic databases in invocation-owned directories under this service folder and remove those exact directories. They do not inspect recorded data, bind sockets, contact hosts, or call model APIs:

```text
python -B -m unittest discover -s stack/services/home-context -p test_home_context_service.py -v
```

`producer_adapters.py` wraps explicitly selected observation dialects with commissioned clock/root bindings and a caller-owned closed predicate mapping; it is not a complete extractor for every legacy model record. It rejects unknown dialects, uncommissioned clocks, unbound roots and observation-local IDs used as persistent tracks. It preserves receipt TTL and physical dependence across model interpretations. `to_geometry_sidecar` copies explicitly supplied registered metric geometry from one physical root and qualified scoped person, with matching registration/coverage/digest; it refuses multiple sensors and performs no coordinate transform or target inference. The mapper consumes registered semantic relations and does not consume this sidecar. No Lab producer or existing relied-on service is connected by this change.

The separate `test_shadow_worker_integration.py` checks synthetic loopback HTTP between the Node worker and journal, declarations, atomic commits and worker restart. It binds only a fresh local test socket and may require sandbox approval. It contacts no AI host or model and establishes no native, physical, or continuous production acceptance.

## Opt-in initial live producer profile

The additive [live engagement worker stage](../../../contracts/home-live-context/WORKER.md)
uses explicit config/manifest version 2, separate schema pins and a private worker
grant. It supplies current input cuts, typed atomic commits/checkpoints and
consumer-neutral engagement with unavailable lighting delivery. The producer-only
version-one behavior below remains unchanged; see the
[worker procedure](../../../docs/runbooks/home-live-engagement-worker.md).

`--profile home-live-shadow-1` selects an isolated producer-ingress profile for
`home-live-fact-1` and separately granted `home-live-geometry-1`, plus explicit
existing revocation controls. The generic API/worker descriptions above apply
to their respective historical profiles; config/manifest version one refuses intents,
feedback, evidence requests/replies, worker commits and future live output
families. It acquires no camera/model data and makes no lighting decisions.

The [profile/scene stage](../../../contracts/home-live-context/SCENES.md) selects
config3 and adds attributed intents, durable settlement and approved scenes.
The [stationary-delivery stage](../../../contracts/home-live-context/STATIONARY-DELIVERY.md)
selects config4 and independently validated virtual execution. Explicit config5
retained aiming, config6 correction/lifecycle, and config7 conservative delivery
and bounded-clock successors each require their own complete pins/grants.
The [resident consumer](../../../contracts/home-live-context/RESIDENT-VIEW.md)
uses Home's existing Apartment view; it adds no second engagement authority.
These local controlled software implementations do not establish real Den
accuracy, continuous production capacity, deployment or physical qualification.

The [live ingress operator procedure](../../../docs/runbooks/home-live-context-ingress.md)
documents exact CLI configuration, manifest/source/clock/registration pins,
separate source-control grants, bounded original-receipt catalog or trusted
resolver composition, current-head/dependency retirement and restart/rollback.
Use a fresh database and absolute UTC; an exact compatible live restart preserves
durable lineage and fences without refreshing evidence. Wire clocks support only
`unix_ns,unix_s,device_ns,monotonic_ns`; runtime probabilities must be null.
The [live contracts](../../../contracts/home-live-context/README.md) are additive;
historical schema bytes and profile behavior remain unchanged. Local synthetic
HTTP verification does not establish Den accuracy, deployment or continuous
operation. Durable root/identity/session caps can be reached before retention.
