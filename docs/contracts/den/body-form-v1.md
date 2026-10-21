# Home body-form consumer v1

## Shelved by owner: skeletal tracking only for now

The owner rejected the continuous human-mesh candidate and explicitly chose to
stick with skeletal tracking for now. This supersedes the body appearance work
below as an active requirement. Do not integrate, package, merge or deploy the
capsule or human-mesh presentation in draft #412. Retain the research, prototypes
and scoped evidence for possible future use; they are not accepted appearance.

The skeletal producer/consumer lane in #410 remains the active path, including
its independent timing and coalesced-stream fixes. Body fitting, volume/shadow
integration and the rejected capsule depth comparison are deferred; they do not
block current skeletal work unless a defect also reproduces in that renderer.
Deployment remains on hold. The remainder of this document is historical.

## Visual acceptance correction

The owner rejected the current capsule/ellipsoid screenshot. The implementation
below is a diagnostic volume prototype, not the accepted human appearance.
Passing its transport, geometry, pixel and GPU checks does not establish visual
acceptance. Draft PR #412 must not be presented as completing the body-form goal.

The target is a continuous, anatomically plausible human surface in monochrome,
roughly sized to the person, without requiring their likeness or skin texture.
The replacement must use an appropriately licensed anatomical mesh with a skin
rig or a parametric human body model. Increasing capsule tessellation, merging
primitives or changing their material alone does not satisfy this correction.

Local acceptance requires all of the following:

- Inspect the actual rendered mesh in neutral standing, seated, crouched and
  raised-arm poses from front, side and three-quarter views. The neck,
  shoulders, armpits, elbows, hips and knees must deform continuously without
  the disconnected mannequin silhouette or gross self-intersections.
- Verify approximate scale and plausible proportions from supported inputs;
  disclose nominal shape and unresolved size. A generic high-quality mesh can
  precede camera-based shape fitting, but must not be called a measured fit.
- Drive the mesh through the real mounted Home consumer with authored poses,
  then separately qualify supported live poses. Sparse surface observations do
  not supply a complete anatomical rig or hidden-body evidence by themselves.
- Keep unavailable or inferred pose evidence explicit. Do not retain limbs,
  identity or shape between unrelated frame-scoped detector IDs. A future
  persistent fit requires the producer continuity contract first.
- Verify the body responds to the actual modeled spotlight direction, intensity
  and occlusion path. A fixed matcap only proves shape readability. Normal room
  depth alone does not prove dynamic shadows on the scan/splat or body-aware
  aiming; track those integrations separately until they have rendered evidence.
- Capture visible-motion/deformation evidence and compare GPU costs against the
  same scene without the body. Do not trade source expiry or timing admission
  for smoother appearance, or equate an authored room with actual-room acceptance.

No replacement mesh, fitted-shape producer or spotlight integration is delivered
by this acceptance correction. Existing runtime bytes are unchanged.

This is a frame-scoped renderer contract, not measured anatomy or tracking
identity. It builds on the explicit unmerged #410 source
`7d513c002280a4463ea917aada11bda7c87a3330`. The producer's separate
`home-body-observations-1` diagnostic extension is not a fitted body pose.
No producer currently promises a hidden pelvis/leg fit.

## Existing envelope

Keep `home-fast-tracking-1`, the authenticated same-origin transport, exact
frame/scene binding, source clock intervals, original evidence expiry and all
three false authority flags. Existing `people[].joints[]` means depth-supported
visible surface observations in apartment Z-up metres; these are not qualified
anatomical joint centres. No image, camera transform, floor snap, physical
command or new persisted application field is introduced by this consumer.

`instance_id` is a per-frame detector/crop identity. It and array position must
never establish continuity. This implementation carries no pose or limb state
between packets, regardless of repeated IDs. Anonymous temporal association
requires a separately reviewed producer-issued track reference, generation and
continuity state; those fields are not supplied or inferred here.

## Optional fitted pose (authored consumer tests only today)

`people[].body_pose` may contain:

```json
{
  "schema": "home-body-pose-1",
  "frame_key": "<exact enclosing 64-hex frame key>",
  "frame": "apartment_z_up_m",
  "joints": [
    {"id": 0, "status": "observed", "position_m": [0, 0, 1.6]},
    {"id": 11, "status": "estimated", "position_m": [0.1, 0, 0.9], "method": "producer-fit-version"},
    {"id": 15, "status": "missing", "position_m": null, "reason": "outside_frame"}
  ]
}
```

The shortened example illustrates record types; an actual payload requires all
17 unique COCO joint IDs. Observed entries must match the enclosing observed
joint coordinates within 1 micrometre of serialization rounding. Estimated
entries require a nonempty method (at most 64 characters) and cannot replace
an observed entry. Missing entries cannot have a position or contradict an
observation. Reasons: `outside_frame`, `low_2d_response`, `depth_unavailable`,
`depth_rejected`, `occluded`, `not_provided`. Invalid optional poses are refused.

The containing envelope governs scene, source identity and expiry. A fitted
pose cannot refresh those limits, qualify registration or grant authority.
Raw RTMPose SimCC responses are not calibrated probabilities; this consumer
does not reinterpret them as confidence thresholds.

## Presentation and refusal

A plausible shoulder pair plus head or pelvis anchors is required. Missing
pelvis evidence yields a visibly estimated neutral upper-chest volume, without
inventing pelvis/leg joint coordinates. Missing shoulder evidence refuses the
form. Outlier limb lengths are omitted with a diagnostic reason; they are not
stretched across the room or joined to another detection. No implicit
interpolation, foot contact, floor constraint or stature calibration is used.

Head/torso/limb volumes are approximate, even when all their anchors are
observed. Explicit estimated joints and neutral hands/feet/chest shapes use
wireframe material. Normal room depth is retained. Estimated shapes do not
write depth or cast shadows; observed-anchor volumes may participate in normal
scene depth but do not establish physical occlusion or alter the precomputed
scan lighting basis. Actual dynamic spotlight/scan shadows require separate
lighting integration and verification.

Observed-anchor volumes use neutral monochrome matcap shading so their 3D
shape is readable even when the scan has no scene lights. This presentation
does not add room lights or claim photometric response to the spotlights.

The minimal dock status reports body/partial/estimated/unavailable and
approximate size/placement. Existing Details exposes drawn-update cadence and
camera-clock-to-GPU bounds. These are actual submitted/GPU-completed updates,
not producer cadence, display-photon timing or interpolated animation speed.
Captured pixels and private visual inspection additionally verify visibility.

Authored native-GPU checks use a 1366 x 768 desktop viewport. The software
renderer uses the same desktop layout at 960 x 600; a larger CPU canvas made
otherwise valid hosted clock responses take 1.24–1.37 seconds, correctly
failing the unchanged one-second clock gate before streaming. This fixture
load reduction changes no production setting, evidence expiry, 15-second
receipt wait or GPU deadline. The first qualified receipt must be unexpired.
Depth comparisons toggle only body drawing while preserving the source/UI
state, isolating normal depth from animated survey decoration and layout.

## Ownership and acceptance

UI owns `body-form-model.mjs`, `body-form-renderer.mjs`, the narrow body
construction/disposal/readback seam in `fast-tracking-layer.mjs`, and the
`fastStatus` header in `home-apartment-den-perception.jsx`. Producer owns depth,
same-frame 2D diagnostics, camera-to-room correctness, association and services.
Clock/expiry/stream/GPU semantics remain those of #410.

Authored standing, seated, partial, estimated and two-person fixtures validate
the mounted Home consumer, actual GPU draws/pixels, normal controls, scene
mismatch/expiry withdrawal and buffer cleanup. They do not establish a correct
live body or sustained latency. Current live evidence lacks full torso/leg
coverage and room registration remains unresolved. First match visible head,
shoulders and arms against the exact camera frame using producer diagnostics.
No merge, deployment, restart, physical output, pack or registration change is
authorized by this local implementation.
