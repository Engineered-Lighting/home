# Live profile binding and scene continuity

This additive stage is `home-live-shadow-1 / profile-scene-worker-1`. It requires
explicit `home-live-ingress-config-3` / `home-live-ingress-manifest-3`
commissioning. Producer schemas and the engagement-stage schemas remain pinned
and unchanged. There is no automatic config/checkpoint migration.

The new [schema bundle](scene-schemas/DIGESTS.json) pins a closed
`home-live-intent-event-1`, `home-live-shadow-decision-2`, and version-two private
checkpoint, commit and input-cut types. Engagement remains
`home-live-engagement-state-1`. A separate `scene_interface_digest` and
`scene_schema_pins` bind this extension; the producer and engagement interface
digests keep their original meanings.

## Responsibilities

The shared engagement reducer remains the sole temporal engagement authority.
The scene module consumes settled relationships without a second entry dwell.
It selects one immutable approved composition and retains its scene ID, example,
nominal command level and CCT across compatible movement and activity refinement.
Only explicit bind/unbind input may replace that composition within an episode.
Neither a selected example nor an intent establishes observed activity truth.

`nominal.brightness_fraction` is a command fraction, not measured illuminance.
The scene has an approved fixture-family reference, not a chosen fixture or
motor coordinates. Allocation, execution and display-beam lease remain
unavailable/withdrawn. A requested goal is not delivered illumination.

## Approved catalog and identity

The worker policy adds `scene_policy`, with an immutable bounded catalog of
approved profiles and lighting examples, an independently approved anonymous
baseline, and an approved smooth presence-background goal. Profile records pin
the complete example records by canonical JSON SHA-256. Example records carry
nominal level/CCT/family and a requirements reference. No preference recognition,
biometric identity, learning or aesthetic exploration runs in this stage.

Bindings refer to the exact site, room, session, domain, track epoch and anonymous
PID. They cannot transfer across track resets, ambiguity, authority/source
withdrawal or incompatible commissioning. Bindings cannot carry an earlier
episode's corrections into a new episode. Scene/private binding state is
checkpointed separately from the shared relational reducer.

## Public interface

Existing authenticated routes retain their legacy behavior unless config3 is
explicitly selected:

```text
GET  /api/home-context/v1/live-scene-catalog?site_id=...&room_id=...
POST /api/home-context/v1/intent-events
GET  /api/home-context/v1/intent-events/{id}
```

The catalog is immutable manifest-scoped metadata, not observations. Intent
payloads have the frozen handoff's direct full `PersonRef` as `target`.
`bind_profile` supplies exact `profile_ref` and `example_ref` pins;
`unbind_profile` has `value:null`. Scope and actor are authenticated, not inferred
from a browser selection. Human grants do not grant camera acquisition or
private worker checkpoint access.

Ingress returns a durable `accepted` receipt. This does not mean applied. Lookup
returns the original receipt and an independently durable
`home-intent-application-result-1` when the sole worker commits an applied or
rejected result. Its decision reference resolves to that exact consumed decision.
Exact retry preserves the original receipt; a changed body under the same origin
and ID rejects. Lookup is actor-isolated. Expired pending intents still reach a
terminal rejection rather than disappearing from the queue.

The causal input cut includes current observation/geometry heads and unsettled
intents. Original intent bodies survive event-page pruning until durably settled.
Typed commits atomically store checkpoint, decision, settlements and retry
receipt. Transport cursor, semantic sequence and decision sequence remain
distinct. Intent observations never enter original sensor-root provenance or
the engagement reducer's evidence cache.

## Presence and continuity

A fresh authorized cheap `person_presence:present` fact requests a separately
approved smooth background goal immediately. It creates no activity episode,
scene or working target. Source failure/expiry is not observed vacancy. Background
and task goal status remain independently visible.

Supported generic engagement can select the approved anonymous baseline without
inventing personal identity. An explicit current-track binding selects that
profile's approved example. Later exact activity refinement does not creatively
reselect the scene. Brief uncertainty can retain composition identity privately
within the bounded gap policy; absent fresh support does not mint a fresh task
goal. Multiple eligible relations remain explicitly unresolved by the singular
scene consumer, while the shared reducer retains their separate relationships.

## Validation and rollback

Regenerate/check only this stage using
`node tools/build-home-live-scene-schemas.cjs [--check]`. Existing producer and
engagement generators must still pass unchanged. Public journal/Node integration
tests use fictional scoped observations and approved examples; they do not
qualify real perception, optics, hardware or installed operation.

Stop the scene worker and remove its grants/config to roll back. Keep the
historical journal and original receipts. Recommission the earlier producer-only
or engagement stage explicitly; never reinterpret a version-two checkpoint or
decision as version one.
