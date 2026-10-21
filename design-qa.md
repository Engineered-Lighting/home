# Home design fidelity verification

final result: blocked

The remaining blockers below are evidence and approval gaps for complete ZIP
acceptance. The single-pixel native capture diagnostic is tracked separately
and is not holding up UI implementation, following the owner's instruction.
No merge, deployment, tracking-producer change or body mesh work is authorized
by this report.

## Current scope and source

- Reference: `HomeUIticketBreakdown.zip`, SHA-256
  `4f7fdb568481517e0e34e58459fcdd48f6111aac6b57c8c145c55d57091466c3`.
- Main baseline, rechecked remotely: `d6701024ce6ebc45a94b4d5ac7562574de0efd2d`.
- Fixture/glow predecessor: #411, `f22912312fed3d15d85f7ab2254d1fb22c0fc38f`.
- Current UI runtime: #414, `d850afc6d52ee2efd4c3beed3199e01d55a7dedd`.
  Both PRs remain draft and unmerged.
- The owner's dark-only decision supersedes the ZIP's Light-theme clauses for
  this release. Body mesh, fitting and body occlusion remain shelved.
- The ZIP explicitly retains the production scan renderer and existing Chat,
  action, authority and private-review controllers. Its scripted room,
  proactive actions and example data are not copied into production behavior.

## Local corrections implemented

| Audit ticket | Correction |
| --- | --- |
| #398 | Orb/core/halo follow the reference's channel weights and gradient; redundant survey decorations hide while modeled fixtures show. Source/pick geometry stays intact. |
| #399 | Selected/all/off beam display, independent pools, restrained beam gain and optional details. Hiding fixtures now retires the lighting presentation lease, including an import still in flight. |
| #400 | Compact Light/Aim rows, current/proposed hierarchy, Set/Discard and Undo; grouped presets; 118px phone radial ring with a 44px native equivalent picker. |
| #401 | Direct Setup and compact Lit; normal room toolbar during Setup; Echo Light/Aim/Setup tabs share the existing advanced Aim controller. |
| #402 | Radial Off reads zero and restores stored levels/CCT on switch-on. Protected geometry and channel state contracts remain. |
| #403 | Source-derived short tags, selected-fixture phone tab, compact mode pill, one room toolbar and navigation in Layers. |
| #404 | Compact camera dock actions, single no-session state, idle diagnostics in Details, and Close during discovery. Existing media/session owners remain. |
| #405 | Compact Setup header, source disclosure, readable forms, compact device records and rail clearance. Existing saves, CAS conflicts and read-only refusal remain. |
| #406 | Compact Chat header/actions, retained authority row, compact result cards and readable missing-image captions; one error per failed typed turn; slash-picker Escape preserves draft/focus and typing reopens it. |
| #407 | Home and Previous Home are dark only; the existing preference save normalizes saved Light choices. No new preference schema. |
| #408 | Honest capability copy: scan effects remain modeled; body shape/shadows are deferred; Follow remains capability gated. |
| #409 | Complete 61-ticket inventory with all 101 build and 83 acceptance clauses, original issue links and explicit scope dispositions. Inventory is not blanket acceptance. |

Verification also found and corrected these reproducible defects:

- A full phone sheet lost its aim descriptor and pending Set/Discard state.
  The existing resident model now supplies readback while drawing is paused.
- A retained aim bar obscured radial pickup. Placement now reserves the actual
  hub and reticle bounds without discarding the proposal.
- Escape on an auto-focused Layers checkbox failed to close the popover.
  Checkbox/button inputs now yield to overlay Escape; text/range/native picker
  editing guards remain.
- On 390x460, the equipment-unavailable notice covered room navigation and
  clipped Retry. The compact notice now sits above the toolbar; Retry recovers
  the authored registration without writes.
- A newly attached mesh could be picked before a draw propagated its transform.
  Plain and cutaway surface picks now synchronize the transform first. Existing
  geometry, hit filters, floor-gap logic and independent beam behavior remain.
- A phone action-details target overlapped its label. Compact card columns now
  reserve the actual control width; real pointer clicks open and close details.

## Evidence and verification

All screenshots, room imagery, registrations and diagnostic receipts are
private under the worktree's `.tmp/`. Nothing from those artifacts is published
in issues or the PR. The test fixtures refuse household network/service writes.

### Reviewed visual evidence

The supplied reference files are unchanged and inventory-hash checked. Native
CSS/pixel captures use DPR 1. The retained responsive comparison set includes
1920x1080, 1600x1000, 1366x768, 1024x768, 820x1180, 768x1024, 390x844 and
390x460. Earlier Light-theme captures are historical; they are not this
release's acceptance target.

- Light/Aim/preset, header, compact dock and phone comparisons are in
  `.tmp/header-toolbar-qa/matrix-*`. Room geometry, names, fixture levels,
  selected state and dock contents differ in several pairs. Those pairs prove
  composition only; they are not pixel/state equality or installed qualification.
- Current compact Setup records/rail are in
  `.tmp/header-toolbar-qa/qualified-setup-devices-phone-app.jpg`. The matching
  reference uses Devices, rather than the previously mismatched position tool.
- The missing-equipment notice failed before the layout correction. Its
  post-fix 390x460 image is in
  `.tmp/apartment-phone-equipment-browser/run-1792564310485/phone-unavailable.png`.
- Current populated Chat images are in
  `.tmp/apartment-chat-cards-browser/run-1792565764253/`: desktop, phone and
  short-phone captures. The reference's action/camera Chat states are in
  `.tmp/header-toolbar-qa/chat-*-reference.jpg`. The comparison exposed the
  cramped caption and phone action target, which were corrected and rechecked.
  A fixture that displays an unavailable private-review message does not
  qualify the governed review frame itself.
- Stream/final/cancel/error/recovery captures are retained in the mounted Chat
  fixture run. A final answer appears once, cancellation ignores late results,
  errors recover on a new turn, and revoked authority refuses dispatch.

Fonts/typography: supplied Geist hierarchy and compact labels were reviewed;
retained tool/action internals keep their existing controller components.
Spacing/layout: compact card borders, header actions, phone radial equivalent,
Setup rail/toolbar clearance and recovery controls were reviewed at native size.
Colors/tokens: dark glass, warm/current and cyan/proposal states, preset borders
and 4.5:1 informational text/target audits remain in the browser proofs.
Image quality: original scan rendering is retained; the ZIP's illustrative
geometry does not qualify scan sharpness or physical alignment. Copy/content:
missing commissioning, absent registration, offline services and deferred body
features remain explicit; scripted prototype data is not reported as live.

### Local functional evidence

- Surface-picking regression: 8 unit checks; cutaway suite: 20 checks. A newly
  attached transformed mesh failed before correction and succeeds before any
  rendered frame afterward. The Setup proposal browser preserves calibration
  bytes and passes all four steps; proposal fields remain gated.
- Short-phone unavailable equipment: red before correction, green afterward;
  no toolbar overlap, Retry is reachable and recovery performs no writes.
- Compact cards and slash recovery: real mounted Home with authored bootstrap
  events and an authored HA transport at 1366x768, 390x844 and 390x460.
  Captions use the column width; details open by pointer; Escape preserves the
  draft/focus and typing reopens suggestions.
- Retained presenter/action/source contracts: 20/20; slash contracts: 61/61;
  JSX validation: 115/115.
- Precompiled JSX: 3/3 against current runtime, including exact build/loader,
  verified-manifest and per-file fallback behavior; browser/compiler bytes match.
- Canonical radial/orb proof: 37 checks, retaining protected readback, source,
  descriptor, pick, beam and receiver hashes.
- The complete 141-item run at `0b740636` finished all twelve shards. Its only
  failing harness was overlay admission; the new pool presentation remained
  allocated with fixtures hidden. That lifecycle fix is in the current runtime.
  The focused overlay proof now passes all nine functional checkpoints across
  Cloud, Photo and Mesh. Its coexistence step explicitly selects All beams,
  because the ZIP's new default is selected-only.
- The complete 142-item run at the current runtime finished all twelve shards:
  140 items passed. Its two failures were the native alpha diagnostic below and
  the new Chat caption test sampling rectangles across different layout frames.
  The caption test now reads all rectangles atomically and passes its original
  assertions at all three viewports. The earlier failure is preserved.
- Hosted CI on that head also hit the unchanged 145-second Cloud renderer suite
  deadline. Cloud now uses separate frame and contract entry points, following
  the existing Mesh/Photo split. Every pose, image threshold, interaction and
  shader-port assertion remains; Cloud frames still draw Mesh once to retain
  the original floor-port build assertion. Both phases pass locally.
- The revised mapped plan has 143 items. A fresh exact-head hosted run and final
  receipt reconciliation remain required. The 142-item run and focused rechecks
  are not presented as one all-green 143-item run. Two combined workers and the
  unchanged 145/180/600-second limits are retained.

The overlay timing samples are wall-clock observations. Their automatic
performance verdict is `requires-review`; they do not qualify a GPU timestamp
blend budget or actual-device frame-rate acceptance.

### Existing local scan review

A separate loopback-only, read-only review loaded the existing Echo Park and
Victoria Cloud, Photo and Mesh assets. The adapter disabled household upstreams,
service workers and all writes. ACTIVE and manifest fingerprints were preserved,
with no server errors or writes. It had no Den equipment registration, so it
could not verify registered fixtures, relighting or a live connection. That
refusal remains a limitation, not a reason to replace private assets or invent
live responses.

## Remaining work before complete ZIP acceptance

1. Complete matched-state fixture/channel/orbit evidence, including the actual
   registered Den fixtures with the installed scans. Existing scan loading and
   authored orb mathematics are separate evidence.
2. Complete paired visual evidence for the remaining populated camera states,
   all Setup tools and Chat aim/camera/world/explain/governed-review/Travel
   surfaces. Existing functional contract coverage is retained; missing images
   are not silently counted as passes.
3. Reconcile the final combined receipts and exact-head CI, then update each
   audit ticket with its bounded result. Original 61-ticket requirements and
   existing implementation credit remain in the ledger.
4. Resolve proposed stored fields only with the owner's approval: short saved
   labels, hand-placed camera pose and mmWave records. They remain off by default.
   Den registration changes require their own clearance.

The native screenshot diagnostic observed one pixel with alpha delta 5 where
its unchanged limit is 4; RGB remained within 1. Its cause is not established.
No threshold was loosened and no renderer fix is claimed. The owner explicitly
said not to let this separate diagnostic hold up UI work.

Actual Mac/iPhone, live upstream, inference accuracy, physical commands,
photometry and body-mesh acceptance are not established by this local report.
No deployment or merge has occurred. The skeletal tracking chat was not contacted.

## Comparison history

Earlier per-batch reports and failed attempts remain available in Git and private
receipts. This report replaces stale remaining-work lists that still described
now-corrected header, phone, Setup, beam and Chat gaps. The current findings above
control the acceptance verdict.
