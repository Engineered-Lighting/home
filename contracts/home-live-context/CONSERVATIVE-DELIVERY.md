# Conservative delivery and causal repair successor

This is an explicit local software successor, not installed or real-Den
qualification. Select config7/manifest7, worker6, decision/checkpoint/commit/cut6,
scene-state3, delivery policy4/snapshot4, resident binding6, prepared entry3 and
bridge3 together. Intent2 and lifecycle1 remain byte-exact leaves. All prior
schema families and checkpoint meanings remain historical inputs; no implicit
upgrade or mixed grant is admitted.

Managed producer provisioning2 separately pins the incumbent LA capability with
`la_binding` (nullable when unavailable): `home-live-existing-la-binding-1`,
version1, the admitted `model_ref` and separate `artifact_sha256`. The model
reference digest identifies configuration; it is never treated as an artifact
hash. Both must match their respective admitted policy/capability. Provisioning1
keeps its previous shape and supplies no inferred LA binding.

## Clock and causal cut

Manifest7 requires a `home-live-service-clock-policy-1` with an independently
supplied qualification reference and exclusive expiry, absolute uncertainty,
anchor-divergence, read-round-trip and mapping-age bounds. The journal checks
wall versus monotonic progress and latches loss of qualification. Constant
absolute offset requires that independent qualification; a server sample cannot
establish UTC accuracy by itself. Source, worker and acquisition restart classes
retain their distinct recovery requirements.

Snapshot, event-page, worker checkpoint and raw input-cut6 responses supply
`home-live-service-clock-1`: exact site/room/session/domain, stream epoch,
authority generation, original anchor epoch, sampled UTC, uncertainty and
qualification status. Consumers bracket successful scoped reads with a local
monotonic clock, include read latency, reject stale/wrong-scope/generation samples,
and invalidate on disconnect, local discontinuity or expired mapping. A component
remount cannot clear a page latch. A new page needs fresh qualification.

Receipt6 records the original bounded first-consumer interval:
`received_at_lower_bound`, conservative upper `received_at`, and half-width
`clock_uncertainty_s`. The upper bound must be after original journal delivery
and at or before the decision. A worker waits for that causal bound before its
tick; it never backdates the receipt. Worker checkpoint restoration also uses
the qualified service mapping. Exact retries preserve original bodies and times.

Decision input-cut6 includes exact input receipt membership, complete raw
geometry references and `source_generations`, covered by its cut digest. A
changed source generation or authority cannot hold an earlier beam. Independently
validated output must agree with the same complete cut even while dark.

## Geometry and delivery

Policy4 separates useful work range from `modeled_emission` and independently
qualified `observation_coverage`. Their assessment references and expiry join
fixture, room, protection and coverage assessments in that order. The assessed
coverage must enclose the modeled emission within the assessed room. This is a
bounded virtual model, with no physical zero-light claim outside that domain.
Every known protected person remains a hazard regardless of useful work range.

Only the exact typed capture-metadata shape can be excluded from person
completeness: null person/subject, not-applicable association, no candidates,
capture method/head, and all eight unavailable parts. An unresolved person or
missing anatomy is never capture metadata merely because identity is null.

Each fresh immutable geometry cut revalidates a retained aim, obstruction,
uncertainty, full protection, mechanics and lease. Failed support withdraws;
retargeting occurs dark. Pan is commissioned degrees and tilt is from downward
minus Z; reachable equivalent pan branches and signed zero have shared independent
test vectors. Geometry changes do not create another dwell or scene.

The browser renders only committed sampled aim, brightness and CCT. Origin,
cone envelope and range come from commissioning. A prior sample can bridge a
new commit's uncertainty window only inside its original lease and with the
same scene/aim/source generations/authority. Withdrawal, changed hazard or scope
invalidates it immediately. Opaque depth clipping and visible brightness do not
establish receiver illumination or photometry.

## Lifecycle and bounded retention

Original overlapping availability may bridge a sampled expiry; actual gaps
earn no dwell. Qualified continuity cannot override newer applicable exact-person
absence/departure. Track reset creates no inherited binding, correction, dwell
or hazard clearance. The single reducer remains temporal authority; journal
validation independently checks admitted evidence and committed invariants.

Scene3 restores the entire baseline profile/example choice when appropriate,
ends only the bound relationship, and cannot create a scene from `ending`.
Background need has its derived goal identity and a positive lease bounded by
output age and evidence; background delivery remains separately unavailable.

Current heads survive publication-history pruning using their exact durable
receipt revision. Missing revision evidence refuses explicitly. Delivered bodies
retain only latest-cut and durable-checkpoint dependencies within explicit
count/byte budgets. The last checkpoint may retain expired bodies for restoration;
this never renews support or clears hazards. SQLite contention is retryable
`journal_busy` 503. Exhausted immutable/body/commit budgets refuse explicitly.

Ordinary replay needs original records, consumer receipt intervals, barriers,
ticks, input cuts, intent settlements and commits. Missing originals or unresolved
durable effects yield incomplete evidence. Acquisition-process loss does not
inherit the worker-restart claim.

Controlled validation and limitations are in the
[repair receipt](../../docs/plans/first-live-den-replan-2026-10-15/BLOCKING-FIXES-IMPLEMENTATION.md).
