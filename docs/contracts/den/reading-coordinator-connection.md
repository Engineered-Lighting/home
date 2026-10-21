# Reading observations to virtual Den spotlights

The activity loop publishes original, bound `activity` and `person_pose` events
through the existing authenticated brain event route. The coordinator requires
compatible capture/session/reader context and current pose, applies the reading
hold and sofa proximity rule, selects an above/behind fixture with head-clearance
and aim feasibility checks, and emits its virtual target. Home's existing brain
client/follower applies that target through the virtual-fixture overlay.

The target is a shoulder-derived hand/book-region proxy. It is not a measured
book location. No body mesh, fast-view packet authority, physical output or
identity inference is added by this connection.

## Compatible rollout

Install the compatible brain first. Its health route advertises
`activity-lighting-events-2` and `reading_requires_bound_evidence`. Legacy events
remain useful for occupancy/ambient and cannot activate a reading spot. Then
install the compatible Lab producer, verify original validity and source binding,
and explicitly resume the previously operator-stopped loop under the approved
operation. Preserve the active v4 pack, model configuration and development
alignment policy. Do not clear monitor stops, renew expired evidence or strip
bindings to force reading.

The source has a regression running authored reading/pose observations through
the real `ActivityLoop`, `BrainPublisher`, brain reducer/aim planner, Home client,
follower and virtual-fixture overlay. It verifies the hold, target movement,
expiry and reader replacement. These are controlled software results, not live
reading recognition, correct room placement, rendered photons or comfort.

The inspected Home consumer already contains the compatible client/follower and
render-evidence seam. A backend-only rollout can therefore use the unchanged
Home UI after confirming those deployed file hashes again. Any changed deployed
artifact invalidates that comparison.

## Live result to earn

Correlate the actual model answer and original evidence with accepted brain
events, the selected fixture/target, successful Home aim/application and an
actual completed-render receipt. During normal Den reading, judge beam placement
with the owner; separately observe release, ambiguous occupancy, source gaps and
manual override. Missing image/depth support or an expired model answer must
remain an explicit refusal. Continuous operation still needs its required soak.
