# Live engagement worker stage 1

Ticket [#207](https://github.com/Engineered-Lighting/home/issues/207) adds the
explicit `engagement-worker-1` stage of `home-live-shadow-1`. It does not broaden
the original producer-only configuration. Existing producer schemas, interface
digest, historical profiles and the signed handoff remain unchanged.

The new self-contained schemas and pins live in `worker-schemas/`. Generate with
`node tools/build-home-live-engagement-schemas.cjs`; verify with `--check`.
The five roots are:

- `home-live-engagement-state-1`: coordinate-free state for one full anonymous
  person and stable subject, including episode, selected support, alternatives,
  refinement and exact causal watermark.
- `home-live-shadow-decision-1`: one room evaluation tick, with lighting sections
  explicitly unavailable and a withdrawn display lease in this stage.
- `home-live-worker-checkpoint-1`: private typed temporal state and input receipts.
- `home-live-worker-commit-1`: sole atomic output/checkpoint transaction.
- `home-live-input-cut-1`: private atomic read of current producer heads; it is not
  an event publication or another inference authority.

The worker manifest pins its base producer interface, handoff digest, full worker
schema graph, five schema byte hashes and two permitted publication families.
Only a worker commit can mint engagement or decision events. Producers cannot
post these types directly. Checkpoint, commit and input-cut types cannot be event
publications or semantic-reader escape routes.

## Explicit commissioning extension

`home-live-ingress-config-2`, version 2 has the same outer fields as config 1:
`schema,version,manifest_ref,manifest`. Its `home-live-ingress-manifest-2`, version
2 retains the original identity, handoff/base interface, sources and limits and
adds `worker_interface_digest` and `worker`.

The worker policy has exactly:

```text
worker_id, producer_id, producer_epoch, revision, config_digest,
source_pins, schema_pins, max_output_age_s, semantic_interval_s,
engagement_policy
```

`source_pins` maps every source ID to the canonical digest of its commissioned
source policy. `schema_pins` is the exact five worker schema hashes.
`semantic_interval_s` is one second. `max_output_age_s` is explicitly commissioned,
positive and at most three seconds; evidence can impose a shorter horizon.
There are no inferred accuracy or physical-runtime defaults.

`engagement_policy` explicitly lists `support_methods`, `refinement_methods`,
`negative_methods` and boolean `allow_experimental`. Every method resolves to an
existing commissioned source method. Experimental admission remains an operator
decision, does not confer calibration and cannot set acceptance flags true.
The MVP PC attribution route is not admitted merely because a device is active.

`describe(seed)` in `tools/home-live-context-worker.cjs` returns the pure worker
configuration digest, source/schema/code hashes and worker interface digest.
The digest binds version/profile, full room identity, worker/producer/revision,
source/schema/code pins, worker interface, engagement policy and schedule/lease.
It excludes the enclosing manifest reference, avoiding a circular digest when
the manifest embeds that worker policy. The enclosing manifest is independently
hashed and checked on construction, restore and commit.

Config 1 still refuses worker rights and worker-stage families. Config 2 is an
explicit fresh journal/commissioning choice, not an in-place reinterpretation
of an old producer journal or checkpoint.

## Authority and publication

Use a separate private worker grant with operations exactly `read,worker_commit`,
the expected worker/consumer ID, exact producer ID/epoch, room/revision, every
input source and live binding capabilities `read,worker_commit`. Its schema pins
and whole-type permissions must include both producer types and the five worker
types. Producer event/control grants remain independent.

A semantic reader needs only explicitly granted engagement reads. Full decisions
require geometry authority even though this first stage withholds delivery.
Checkpoint and input-cut reads require the private worker capability. No field
redaction, forged source ID or coordinate-bearing checkpoint projection is used.
The service enforces one commissioned logical room worker, cursor/sequence CAS
and current authority; a competing writer cannot silently take over its history.

The existing routes remain:

```text
GET  /api/home-context/v1/live-input-cut?site_id=...&room_id=...&consumer_id=...
GET  /api/home-context/v1/worker-commits/{worker_id}?site_id=...&room_id=...
POST /api/home-context/v1/worker-commits
GET  /api/home-context/v1/live-worker-commits/{commit_id}?site_id=...&room_id=...
```

The new input cut returns only current original fact/geometry publications,
transport cursor/epoch, room authority generation and per-source generations.
Unlike an event page, it cannot select an older positive when a newer head is
unobservable or withdrawn. The journal records exactly which original inputs it
returned to this authenticated worker. Each decision receipt records
`cursor,id,producer_id,payload_digest,committed_at,received_at`. Admission checks
those exact identities/times and complete current cut before atomically storing
checkpoint, all per-relation engagements, baseline decision and immutable retry
receipt. A changed source/authority between read and commit rejects the result.

## Stage-specific closed variants

The signed handoff defines full decision ownership; #207 implements only its
consumer-neutral baseline. `scene,goal,allocation,execution` are exactly
`{status:"unavailable",reason:"not_implemented"}`. Pending intents and settlements
are empty. `display_lease` is withdrawn with no geometry/protection refs and
`valid_until=sample_at`; all actuation/acceptance flags are false.

This stage explicitly permits **`engagement_ref:null` when there is no unique
relation**, extending the handoff's reference alternatives for the unavailable
baseline. It invents no person or primary occupant. With exactly one relation,
the field binds that same commit's output ID/producer/digest. Multiple relations
are all published separately; no lighting choice is implied by their ordering.
Future lighting variants need a separate reviewed schema/version extension.

The checkpoint's engagement slot closes reducer state, original delivered input
receipts, journal epoch, last decision, code hashes and per-source generations.
Private support/refinement clauses include exact `source_ids`, so a source restart
cannot preserve its temporal credit even when the producer reuses person/subject
IDs or an empty observation gap fell between ticks. Other sources retain their
own state. Immutable root times and physical bindings survive same-domain resets.
Unknown config/schema/source identity cannot silently erase that ledger.

`last_committed_at` is the logical prepared tick time stored atomically with that
checkpoint. Actual durable availability remains the separately returned journal
receipt's `committed_at`; consumers cannot backdate output eligibility to the
logical field. No future service receipt time is fabricated before commit.

## Temporal meaning

The registered-surface and live-subject adapters share one temporal state machine
in `HomeEngagementReducer`. Live facts are not renamed into legacy surface IDs or
scripted dynamic-book assertions. The unchanged defaults are five-second entry,
twenty seconds of observed negative exit, ten-second refinement entry and thirty
seconds of uncertain refinement retention.

Qualified progressing interaction can establish generic engagement with unknown
activity. Proximity, visible material, active devices or activity words cannot.
One old frame under multiple event/model IDs adds no dwell. Correlated camera
derivatives keep their original physical ancestry. Explicit supported negatives
conflict with every applicable positive clause; missing evidence is not a negative.
Each alternative retains its own evidence and expiry. A closed book can clear
reading while generic supported holding remains possible.

Only a selected clause and its required dependencies bound current support.
Consumer eligibility is no earlier than producer availability, journal commit
and first actual consumer receipt. Source age/clock uncertainty only reduce
validity. The private root ledger retains original times and physical bindings;
checkpoint restore cannot rewrite or discard them in the same domain.

Compatible restart restores still-current temporal state. Expired support or a
retained-cursor reset requires fresh dwell while retaining immutable bindings.
Source generation changes reset only dependent temporal state. Unknown commit
acknowledgement blocks recomputation: retry the exact body or reconcile its durable
receipt and checkpoint first. Expiry evaluations continue with no new publications.

This stage establishes controlled software behavior. It does not establish
real perception correctness, continuous deployed headroom, wanted lighting,
optical delivery or physical acceptance.
