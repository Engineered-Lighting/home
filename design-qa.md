# Home design fidelity verification

final result: blocked

This result applies to complete ZIP fidelity. The fixture/glow/Light correction
batch is implemented locally; the remaining audit findings are still open in
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

## Remaining acceptance work

- #398: finish the complete every-state/orbit/theme fixture visual matrix on
  registered scans; this local authored comparison does not qualify installed
  Echo Park or Victoria assets.
- #400: compact Aim hierarchy, radial preset pill styling and remaining exact
  row spacing. This batch fixes the Light label/track/output portions.
- #399: restrained beams and redundant modeled annotations.
- #401, #403, #404: header/Setup/toolbar, phone label/tab collisions and camera
  dock fidelity remain outside this correction batch.
- #405-#409 retain their audit scope, including gated Setup tools, Chat density,
  the theme decision, capability copy and complete acceptance evidence.

The older October 18 comparison images remain historical evidence under
`docs/design/apartment-unified/evidence-20261018/`; they do not prove current
whole-app fidelity. Deployment stays on hold. No tracking-pipeline files or
other chat are part of this change.
