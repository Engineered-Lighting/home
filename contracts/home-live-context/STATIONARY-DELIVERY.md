# Stationary protected virtual task delivery

Additive stage of `home-live-shadow-1`, following the
[scene stage](SCENES.md) and [resident view](RESIDENT-VIEW.md).
The frozen first-live handoff and historical schema bytes are unchanged.
This is modeled software delivery, not measured illumination, live Den
perception qualification or permission to operate a physical fixture.

## Explicit commissioning and versions

The historical #209 stage selects `home-live-ingress-config-4` and
`home-live-ingress-manifest-4` together.
The manifest retains producer, engagement and scene interface pins, and adds
`delivery_interface_digest`. Its worker retains the existing policy and adds
`delivery_policy` and exact `delivery_schema_pins`.

The new closed families are `home-live-shadow-decision-3`,
`home-live-worker-checkpoint-3`, `home-live-worker-commit-3` and
`home-live-input-cut-3`. Generate/check them with
`tools/build-home-live-delivery-schemas.cjs`. Old config1/2/3 readers and
schemas remain explicitly supported. There is no checkpoint reinterpretation.
Producer fact/geometry, engagement and attributed profile intent families
retain their existing meanings and bytes. Every new grant pins its exact
types, scope, manifest, sources and schema bytes; a browser has no worker grant.

`home-live-task-delivery-policy-1` explicitly commissions one virtual fixture:

- Exact inference registration and separately pinned room/canonical collision
  assessment, complete conservative occluder primitives and usable bounds.
  The display mesh or a cutaway is not collision clearance. No automatic
  private mesh extraction or claim of surveyed completeness is provided here.
- Fixture pose pin, explicit origin and right-handed row-major 3x3 local-to-room
  rotation, family/applicability and optical assessment.
- Full modeled optical envelope, pointing error, assessed range and upper spill
  fraction. FWHM or a clear central ray is insufficient. This stage requires
  an assessed zero residual in its modeled dark state; otherwise it refuses.
- Pan/tilt degree limits, velocity/acceleration/jerk bounds, settling/fade times
  and a commissioned dark initial virtual pose.
- Required body primitive IDs, protection policy/exposure limit, independent
  work-coverage threshold and bounded angular-projection grid.

These records are assessed applicability claims, not self-calibration. The
authored fixture used by tests has deliberately fictional assumptions; its
pins cannot be reused as a Den survey or optical assessment.

## Allocation and uncertainty

The worker passes an immutable input cut to the public pure delivery module:
`createDelivery({identity,policy,max_output_age_s})`, then
`step({t,seq,scene,goal,facts,geometries,geometry_refs,input_cut_digest,
authority_generation})`. It does not establish another engagement dwell.

One supplied, qualified working region must match the selected scene's full
person/subject/episode, nominal command and requirements. Body, head, both
hands and protection are separately mandatory. Other known occupants need
complete current protective geometry too. Losing observation is not vacancy.
Current ambiguity or remembered people without qualified geometry blocks
delivery rather than making an obstacle disappear.

Every required uncertainty radius includes its independently commissioned
components plus bounded age/speed/acceleration through the **display lease
horizon**, not just through the sampling instant. The lease is bounded by
all source/part age and expiry, scene/goal, room assessment and output age.

Coverage is an area-weighted angular-projection silhouette proxy of the
inflated work AABB. A cell is clear only when the entire angular cell is
inside the assessed optical envelope and free of conservative room/body/hand
occlusion. Boundary and depth-ambiguous cells count uncovered. Reports retain
clear, blocked and uncertain fractions; partial shadow differs from total
blockage. No page normal, lux, uniformity or comfort is inferred from coverage.
Full cone/spill protection is checked independently from work coverage,
without taking occlusion credit for shielding a head or protected volume.

## Execution and committed display

An allocated proposal contains candidate disposition, coverage, assignment,
policy references, input-cut digest and expiry. It does not establish that
the fixture reached its aim or emitted light.

Virtual execution progresses from its commissioned dark initial pose through
a bounded rest-to-rest quintic movement, settling and nominal fade-in. Motion
and settling are dark. Local elapsed seconds and degree coefficients avoid
epoch-sized polynomial time. A changed stationary work region first withdraws
delivery; continuous retargeting, fixture ranking/handoff and lit travel belong
to the next milestone.

The historical `mechanics.fade_s` field and `fading` phase here describe the
onset fade-in after dark motion/settling. They do not commission a vacancy
fade-out or the glossary's decreasing-light fade duration. Successor brightness
corrections use a separate `brightness_transition_s` ramp, which can increase
or decrease level while preserving scene/CCT. The wire names remain unchanged;
describe the visible operation as fade-in or brightness adjustment according
to its actual segment.

Each 1 Hz atomic commit carries exactly one actual virtual sample at `t`:
`sample_at=interval_start=interval_end=t`. Successive samples show traveled
positions; the viewer neither extrapolates nor displays future endpoints.
New geometry at `t` never retroactively certifies earlier illumination.
This is sampled virtual execution, not a measured continuous control rate.

The decision binds the exact geometry cut, stable scene/profile/example,
nominal settings, assignment, mechanical readback, assessment pins and display
lease. The journal independently validates delivery attribution and mechanics
before atomically accepting checkpoint/output/intent settlements. Lost ACK
recovery uses the original commit bytes and receipt.

Resident binding v2 explicitly selects `model.json` or `seed-model.json` and
supports the commissioned delivery stage; v1 keeps its original exact meaning.
The owned Apartment layer resolves origin and direction from the pinned fixture
and **sampled** angles. A dark sample shows a fixture marker; a positive sample
may show a labeled virtual cone. Source loss, expiry, invalid clock, wrong
registration, malformed samples or withdrawal clears current illumination
locally. Ordinary fixture/beam controls remain fenced, including after Return.

Background presence remains a separate requested goal. Without a separately
commissioned background delivery channel its explicit result is
`background_fixture_not_commissioned`; no task fixture or room-wide illumination
is invented to satisfy it.

See the [operator procedure](../../docs/runbooks/home-live-stationary-delivery.md)
and [milestone evidence](../../docs/plans/first-live-den-mvp-tickets/209-completion-note.md).

## Config7 conservative successor

Config5/policy2 retains an already feasible aim while every immutable current
geometry cut is rechecked; it does not restart motion merely because work-shape
bytes changed. Config6/policy3 adds brightness correction and addressable person
hazard recovery. Config7/policy4 explicitly selects delivery snapshot4, worker6,
scene3 and decision/checkpoint/commit/input-cut6. Its separately generated schema
graph, manifest/interface pins and grants are required together. Historical
config4 does not acquire these changed semantics.

Policy4 adds exact `modeled_emission:{assessment_ref,range_m,valid_until}` and
`observation_coverage:{assessment_ref,method_ref,qualification_ref,applicability_ref,
bounds,valid_until}`. Coverage bounds are a registered metre AABB containing the
assessed story room intersected with the modeled emission sphere's AABB, a
conservative superset of all modeled cone directions in that scope. The fixture
must be inside the assessed room, and modeled emission range must contain useful
work range. Room/collision completeness cannot stand in for independent person
observation qualification. These are applicability conditions, not automatic
survey or calibration. Both expiries bound delivery.

The modeled boundary applies only to this virtual story. It establishes no
physical zero light outside that domain. All known head and protection volumes
are checked regardless of useful-work range; unknown/unobserved space cannot
become empty merely because no person row arrived. Missing applicable coverage
or emission commissioning refuses the new profile. Authored qualification refs
used in software tests do not satisfy real camera/room/optical prerequisites.

The full raw input cut includes paired-capture metadata and the exact source
generation map. Only its closed null-person/null-subject `not_applicable`
metadata form is excluded from occupant completeness; unresolved person rows
remain hazards. Display geometry/protection refs independently cover every
qualified occupant row used, without attributing anatomy to pixel metadata.

An obstacle separated by a coordinate plane from the complete
fixture-to-inflated-work segment hull is irrelevant to that coverage check;
all nonseparated depth-ambiguous obstacles remain uncovered. Equivalent pan
representatives are selected inside the commissioned mechanical range, nearest
the initial pan with a lower-pan tie break. Near-origin degenerate protection
directions conservatively refuse. Nonzero motion uncertainty covers the entire
lease. Binary expiry/age ties lower the lease only to a forward-safe representable
bound; acceptance is not broadened by an epsilon.

The execution assessment refs retain fixture, room, protection and coverage
assessments, followed by modeled-emission and observation-coverage assessments.
The resident adds no authority by displaying them. Real anatomy, containment,
useful-service, accuracy, optical and physical acceptance remain independent.
