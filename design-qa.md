# Home design fidelity verification

final result: blocked

This result applies to complete ZIP fidelity. The fixture/glow/Light and
Aim/preset correction batches are implemented locally; remaining findings are open in
Engineered-Lighting/home#397. This replaces the earlier blanket passed claim.

## Reference and source

- Reference: HomeUIticketBreakdown.zip, SHA-256
  `4f7fdb568481517e0e34e58459fcdd48f6111aac6b57c8c145c55d57091466c3`.
- Visual authority: final SPEC plus unchanged prototype `scene.js` and
  `app.css`. Earlier cylinder prose is superseded by the final orb design.
- Implementation base: main `d6701024ce6ebc45a94b4d5ac7562574de0efd2d`.
- Runtime correction commit: `492019d0`. The prior unmerged Chat correction is
  outside this branch.
- Both previews use authored local services and room data. No household
  connection, registration, pack bytes or physical command is involved.

## Corrected in this batch

The normal fixture view no longer draws the orange socket survey core, ghost,
glow and stem through the modeled orb. Hiding modeled fixtures restores those
survey decorations; source positions and all pick objects remain unchanged.

The orb center uses the prototype's active-channel weighted white. Its halo
uses the prototype's four radial-gradient stops, alpha and radius; six radial
zones contribute their mean, equal in weight to Spot or Down. WebGL and the
WebGPU node path have matching implementations. Existing channel fade, source,
beam, receiver and geometry contracts are retained.

Light rows now use the short Color temp label, with assumed-range disclosure
in help and footer copy. Channel summaries, commissioning badges, the Aim icon
and light-theme temperature palette follow the prototype. Actual range limits
remain authoritative, so thumb positions can differ from the prototype's
illustrative 1800-6500 K range.

Radial Off reads zero in the ring and brightness control while preserving each
stored level/CCT for switch-on. The Off preset and power switch retain their
existing different state semantics.

## Visual evidence reviewed locally

Private evidence: `.tmp/fixture-design-qa-20261021/`. No screenshots are
published. Each pair puts the actual source and implementation in one input;
source PNGs and pairs were reopened for inspection.

| Comparison | CSS/pixel viewport | Evidence |
| --- | --- | --- |
| Desktop Light, dark and light | 1366 x 768, DPR 1 | `desktop-dark-pair.png`, `desktop-light-pair.png` |
| Phone Light, dark and light, half sheet | 390 x 844, DPR 1 | `phone-dark-pair.png`, `phone-light-pair.png` |
| Phone Radial Off, full sheet | 390 x 844, DPR 1 | `phone-radial-off-pair.png` |
| Tablet Cloud and Mesh, half sheet | 820 x 1180, DPR 1 | `tablet-dark-pair.png`, `tablet-mesh-pair.png` |
| Focused typography, track, value and Aim row | native crop, no resampling | `light-controls-focus.png` |

Tablet source and implementation sheet bounds were both y=590/h=590 after
transitions settled. A loading Photo capture was rejected. The final
`tablet-photo-pair.png` is a comparison limitation: the ZIP has no packaged
Victoria Photo scan and refuses it, whereas the implementation loads its
explicitly authored splats. It is not a same-asset Photo fidelity pass.

Fonts/typography: compact single-line labels and existing Geist hierarchy
reviewed at native scale. Spacing/layout: rows fit and values align at all
three widths; smaller residual spacing and preset styling remain below.
Colors/tokens: native pixel inspection confirmed the existing dark warm-to-cool
track already worked; the initial downscaled audit impression was incorrect.
The light gradient now uses the reference palette. Image quality: fixture
cosmetics change while the existing scan renderer stays intact. Copy/content:
real capabilities, missing commissioning and preview-only status stay explicit;
authored names, aims, rooms and data are not copied into the application.

## Functional and renderer verification

- 96 mapped harnesses plus JSX/ESM validation pass (97 plan items). Eight
  disjoint receipts cover the entire mapped plan, with two concurrent workers,
  180-second file limits and a 600-second worker limit. The initial drag test
  expected the old Modeled badge; after updating that assertion and adding
  commissioning coverage, its entire shard passed. The failed attempt remains
  recorded; `mapped/attempt1-shard4.*` is not overwritten.
- 37 radial/orb tests include unchanged protected-equivalence pins for readback,
  descriptors, sources, picks, beams and receiver buffers. A numerical oracle
  checks the new core color and combined halo alpha/channel weights.
- The mounted one-label test also exercises decoration hide/restore and radial
  Off -> 0% -> exact saved levels/CCT on switch-on, with zero command/session
  traffic. Existing mixed-zone restoration and aiming coverage remains intact.
- Native WebGPU and the node renderer's WebGL2 backend pass the complete Cloud
  proof in installed Edge on the NVIDIA adapter: three frame poses, picks,
  projections, fit, fixture/aim readbacks, Photo load and capture. Both report
  no shader-port problems. Evidence: `native-webgpu-edge-fixed.log` and images
  under `native-webgpu-edge/`. Frame-time samples are wall-clock measurements,
  not a GPU blend-budget qualification.
- Native verification exposed a test error: WebGPU's [0,1] clip-depth matrix
  was compared byte-for-byte with WebGL's [-1,1] matrix. The test now checks
  every element against the exact Z-row conversion, retaining its tolerance
  and all interaction/image assertions. The bundled browser's earlier fallback
  was not counted as native verification.

Mapped receipts identify runtime commit `492019d0`; the only subsequent
changes are QA/release notes and the two test corrections above. Runtime source
hashes remain identical across the runs. These are local working-tree receipts,
not an exact-PR-head CI/integration acceptance or authorization to merge.

## Aim and preset follow-up

Runtime: `ecc1932f` and `338275d7`; command-ladder test correction: `6af04674`.
The source ZIP and main baseline above remain unchanged. The earlier 97-item
run describes the fixture/glow batch, not the combined follow-up.

Aim now follows the prototype's compact hierarchy: one header with icon
Undo/Redo, current/proposed rows with warm/cyan markers and right-aligned
distance/tilt, and an inline Tap to aim hint. A valid proposal exposes Set aim
and Discard. Named aims is a separate section. Existing manual Pan/Tilt remain
available in a collapsed Fine-tune aim section. Command keeps its existing
`Modeled · nothing sent` disclosure and transport restrictions.

The four radial presets and two direction shortcuts use the prototype's
grouped 26-pixel pills, 15-pixel radius, four-pixel gaps and selected ice border.
Coarse pointers retain 44-pixel hit areas. Preset behavior is unchanged;
uncommissioned directional shortcuts remain disabled.

Mounted phone verification found an actual bug: expanding the sheet to full
retired the drawing lease and also removed the inspector's aim descriptor,
hiding pending/current rows and Set/Discard. The inspector now uses the
existing fenced resident model while drawing is paused. Tap to aim reveals
the room at half height and retains an exact scene/fixture scope through the
reveal. Hidden documents, changed scenes and changed selections still cancel
the request. No drawing, producer or command authority is added.

Private comparisons are in `.tmp/aim-preset-design-qa-20261021/`. Final captures
use `338275d7` runtime bytes; the later test-only commit does not change them.
The extracted reference's 40 files match the ZIP inventory. Source/app pairs
were composed without resizing and the exact saved pairs reopened:

| Comparison | Viewport / state | Evidence |
| --- | --- | --- |
| Compact Aim, both themes | 1366 x 768 / no proposal | `aim-desktop-dark-focus.png`, `aim-desktop-light-focus.png` |
| Full phone Aim, both themes | 390 x 844 / coarse pointer | `aim-phone-full-dark-pair.png`, `aim-phone-full-light-pair.png` |
| Full phone pending proposal | 390 x 844 / coarse pointer / dark | `aim-phone-proposal-dark-pair.png` |
| Radial presets, both themes | 390 x 844 / coarse pointer / 25% and 50% | `radial-phone-dark-pair.png`, `radial-phone-light-pair.png` |
| Radial presets, desktop/tablet | 1366 x 768 / Off; 820 x 1180 / 100% | `radial-desktop-dark-pair.png`, `radial-tablet-dark-pair.png` |

Typography: Geist/Geist Mono, row labels and compact measurement hierarchy
match the reference. Spacing: Aim removes the nested Named aims inset and
presets stay grouped; phone actions fit with 44-pixel hit areas. Tokens:
warm current marker, cyan proposal marker, ice primary action and selected
preset borders were checked in dark and light. Imagery: existing renderer
and authored fixture data stay intact. Copy: real target names and missing
commissioning/named-aim capabilities remain truthful instead of copying the
prototype's invented data. The additional optical-pivot/travel disclosure and
Fine-tune section remain deliberate functional differences.

The existing coarse-pointer radial ring still stacks at 240 pixels to retain
44-pixel zone targets, while the ZIP stays compact at 118 pixels. This is a
remaining phone fidelity gap; this pass does not claim that entire card is
identical. Phone header/tab and camera differences remain under their tickets.

Focused keyboard, drag and phone regressions pass, including Fine-tune
disclosure, channel-independent edits, Undo/Redo, and full-sheet Set/Undo/
Discard/Tap recovery. The unchanged 145-second browser, 180-second file and
600-second worker limits are retained. CI now splits the complete touched
plan into twelve disjoint shards because an eight-shard worker exhausted
its job budget after its tests had passed; matrix, artifacts and receipt
aggregation all use twelve. Shard-contract tests pass 15/15.

Combined verification status: the final 126-item qualification passes on
`491a26a9af32e663caf79b0dcdab5024ca45385e`.
All twelve receipts are green and their disjoint union equals the unsharded
plan. Two local workers ran concurrently; the longest shard took 158 seconds.
Runtime hashes and the final mapped plan are sealed in the private acceptance
receipt. Subsequent changes only update this QA report; exact-PR-head hosted
CI/integration acceptance remains separate and pending.

Hosted attempt `39527551675` on `4295351e` failed: the workflow contract still
expected eight shards, and a Photo comparison sampled a missing surface pick.
The workflow contract now requires twelve and is part of the local mapped
plan (6/6 focused assertions pass). Photo explicitly awaits collision-proxy
readiness before comparing all 35 samples, requires a real pick per backend,
and compares collision/cutaway state alongside the existing matrices/pixels.
A controlled delayed-collision experiment did not reproduce the exact hosted
failure; its precise cause is not claimed proven. Hosted requalification is
required. Assertions and numerical thresholds remain unchanged.

The first follow-up run exposed an M2 test race: its title predicate passed,
then a later DOM read observed a different loading state. It now captures title
and progress atomically in the same awaited predicate, retaining the original
15-second wait and nonzero partial-progress assertions. That interrupted mapped
attempt is preserved with `ABORTED.md` under `ci-followup-mapped/` and does not
count as a complete run. The separate `final-qualification/` contains the full
126-item green run; the earlier 125-item recheck remains historical evidence.
The first combined run completed 123/125 items successfully; one obsolete
Aim-pane source assertion is corrected, and one native WebGL lit-reference
sample was unstable (RGB delta 18, mean 2.55). Three diagnostic reruns passed
without changing the renderer or its strict image thresholds. That intermittent
native-test condition remains recorded and is not claimed fixed. The failed
run remains under `final-mapped/`; the full recheck is separate under
`final-mapped-recheck/`. An earlier 97-item run was aborted when the phone bug
was found and does not count as a completed combined verification.

## Remaining acceptance work

- #398: finish the complete every-state/orbit/theme fixture visual matrix on
  registered scans; this local authored comparison does not qualify installed
  Echo Park or Victoria assets.
- #400: finish exact row spacing and the coarse-pointer radial-ring layout
  against the ZIP; compact Aim hierarchy and radial preset styling are now
  implemented locally, alongside the prior Light label/track/output fixes.
- #399: restrained beams and redundant modeled annotations.
- #401, #403, #404: header/Setup/toolbar, phone label/tab collisions and camera
  dock fidelity remain outside this correction batch.
- #405-#409 retain their audit scope, including gated Setup tools, Chat density,
  the theme decision, capability copy and complete acceptance evidence.

The older October 18 comparison images remain historical evidence under
`docs/design/apartment-unified/evidence-20261018/`; they do not prove current
whole-app fidelity. Deployment stays on hold. No tracking-pipeline files or
other chat are part of this change.
