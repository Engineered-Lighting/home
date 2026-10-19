# Renderer overlay bridge QA — APT-F01

Functional checks passed with real mode-readiness and real dot-draw assertions.
Performance remains qualified: the corrected Mesh frame-p95 median increases
0.075 ms against a 0.050 ms within-run empty spread. Do not mark the entire
frame-cost acceptance clear. F01 remains draft/partial for this acceptance gap.

This is authored software evidence. It loads generated room assets and survey
records, the real renderer and canonical picking. It is not actual-room visual
acceptance, installed-source verification, live perception or physical evidence.
No household pack, coordinates or private registration is published here.

The desktop measurements below are retained historical evidence. The browser
harness now defaults to a cross-platform `functional` profile at a 390 by 844
CSS product viewport and browser DPR 0.5 (195 by 185 backing canvas in the
authored layout). Its timings establish reduced-resolution functional capacity
only. An explicit `desktop-benchmark` profile retains the original 1366 by
768 viewport at DPR 1 and identical assertions for source-paired comparisons. All nine
stages, mode-readiness/dot-draw/race checks, performance assertions, twelve
120-RAF warm-ups and the 25/145-second deadlines remain unchanged. Profile and
configured viewport/device scale are recorded and asserted in each new receipt;
every sampled window retains `maxFidelity`/`maximum` and its configured DPR.
Actual draw callbacks establish draw admission, not full-resolution pixel
quality. Compact functional CI
results do not clear the desktop frame-cost qualification above. The measured
SwiftShader diagnosis, limits and explicit profile commands are documented in
`overlay-bridge-review.md`; hosted verification remains pending.

The unpaced-RAF trial also failed the hosted global deadline, so that flag is
removed. The current functional profile keeps the three original sustained
120-RAF dot/label/orbit checks with virtual spotlight volumes on, adding real
volume-draw and depth-pass witnesses. Only its other nine warm-ups and its
before/active/after bridge timing windows use the existing native spotlight
display checkbox off. Exact fixture descriptors, channels, targets, bindings
and the registration hash are checked across every toggle; visibility is
recorded and asserted in every warm-up and sample. This hidden state does not
verify R01 or Den interactions. Desktop benchmarks remain full-scene, DPR 1,
display on and browser-default paced throughout.

Functional GPU completion barriers retain context checks and an explicit
post-barrier elapsed check against the unchanged 25-second limit. Live Node
phase/warm-up/window snapshots survive global browser closure. The source and
timing history in `overlay-bridge-review.md` includes the rejected pacing trial
and the single-variable display-off probe. Focused bridge timings do not clear
the retained failed full-scene performance qualification or establish composited
pixel quality; F01 remains partial and the PR remains in draft.

## Allocation correction and measurement follow-up

The follow-up leaf correction reuses one private `Vector3` for depth and clip
math. A distinct world vector still reaches every projection callback, including
nested calls. The exact per-update bridge allocations for zero, one and four
anchors change from 0/3/12 to 0/1/4, plus one scratch vector per layer. This does
not claim zero allocations or isolate whole-renderer CPU/GPU cost.

All viewport/currentness checks retain their placement. The original 24 focused
tests remain, with 12 additional checks for allocation count, nested callback
arguments, callback-triggered host/owner retirement and resize. The unchanged
leaf failed the two allocation targets; the corrected leaf passes all 36.
Engine and picking bytes remain unchanged. Corrected leaf SHA-256:
`f56bdc8d346923dd92a29892d71615ae0e429f6ffaf1e13c7aa9042c1f4d3c7e`.

The historical timing comparison below has two observation limitations: only
active anchors carried draw observers and an extra projection at stats time,
and adjacent 500 ms stats events could share the same 120-frame ring. A separate
matched desktop diagnostic therefore uses identical observers in every
condition, disjoint production-stat rings and one/four-anchor cases. Its draw
and projection witnesses sit outside timing. All raw source-paired results,
including failures, must remain available; allocation tests alone do not clear
the outstanding full-scene frame-cost acceptance.

## Historical sources and receipts

The historical candidate's exact source SHA-256 values were:

| Source | SHA-256 |
| --- | --- |
| `app/src/home-3d/engine.js` | `db26d0fa57cd1c81e808f0b6beb9e40eac0f9b9d94a57cd2a308adbe0d4725b7` |
| `app/src/home-3d/apartment-ui-overlay-bridge.mjs` | `68c17d53b4d6241230739cda97a56d2d306e6e722931251e0f7fa28aa7e2d886` |
| `app/src/home-3d/picking.js` | `ffd237472209a55793e17ed414d10686fbbce4a2cf2a4bb4a7c2991e71316e53` |

Local receipts, in chronological order around the final candidate:

1. `.tmp/apartment-overlay-bridge-browser/run-1792363802603-baseline-before/receipt.json`
2. `.tmp/apartment-overlay-bridge-browser/run-1792364279588-candidate/receipt.json`
3. `.tmp/apartment-overlay-bridge-browser/run-1792364375458-baseline-after/receipt.json`

The baseline phases route only the engine bytes from public commit
`557164592f916cc4f93b79214f560d24d6f797b6`, engine SHA-256
`0e5d4928bc1d7819bd19c6482cee0e56789d85d7005a13e71f267f7358357fcb`.
Their other source files are the current worktree. They are explicitly marked
`public-baseline-engine-only` and are not candidate-source functional evidence.
The normal candidate phase serves current worktree bytes only. The retained
full-shell browser suite and its deadline were not edited.

Earlier candidate receipts are provisional and superseded for dot/readiness
acceptance. In particular, the earlier **+0.125 ms Mesh CPU-render** summary is
superseded by the corrected results below. Historical receipts remain on disk.

## Functional evidence

`node --test tools/test-apartment-ui-overlay-bridge.mjs` passed **24 cases** with
the repository's real Three runtime and real canonical raycaster. Only the
browser's bare Three import is resolved differently for Node. Authored DOM
nodes exercise transform-once projection, finite/depth/viewport rejection,
point/normal/surface-name picks, stable anchor updates, literal labels,
synchronous abort, throwing/reentrant currentness, host retirement, callback
isolation and exact owned-versus-borrowed resource disposal.

`NODE_PATH=<existing Playwright runtime> node --test tools/test-apartment-overlay-bridge-browser.mjs`
passed **9 functional stages** on the actual Home renderer:

- A delayed first module import resolves `null` after consumer abort and
  attaches no DOM or scene group.
- Fourteen admission conditions refuse creation: missing required authorities,
  wrong host, aborted/false/throwing consumer authority, selected-site mismatch,
  changed active revision, registration, frame, collision or scene identity,
  and the retained shadow authority. No rejected condition attaches an overlay.
- The canonical fixture dot and label retain identity and follow orbit changes
  in Cloud, Photo and Mesh. The local dot position stays in the canonical frame.
- Scan picks match the existing point, normal and native mesh name exactly.
- A consumer abort detaches synchronously while rendering is paused. Replacing
  the picking host retires the layer and never retargets its DOM.
- A throwing owner predicate retires only its layer; renderer frames continue.
- Engine disposal retires its layers before shared overlay cleanup. Borrowed
  geometry/material is not disposed by either bridge or subsequent engine cleanup.

Mode strings alone do not establish readiness. The corrected candidate records
945 loaded/visible Cloud points; 945 loaded/visible Photo splats from the
authored `apartment.ply`, with Spark visible; and loaded `mesh.glb` with
`meshFallback === false`. The final baseline-after phase asserts the same mode
readiness. The earlier baseline-before remains explicitly baseline-only evidence.

Exactly **one dot plus one DOM label** is exercised. A temporary real
`onBeforeRender` observer confirms 120 dot draws during orbit in each mode, with
visible ancestry, matching camera layers, valid clipping and visible material.
Separate timed-window witnesses confirm the dot still draws during sampling:

| Mode | Cumulative dot draws at the four stats events | Dot/label visible and attached |
| --- | --- | --- |
| Cloud | 25, 116, 204, 292 | Every sample |
| Photo | 22, 109, 197, 285 | Every sample |
| Mesh | 22, 111, 202, 289 | Every sample |

Aggregate renderer calls/triangles were unchanged in these runs; those counters
are not used as proof that this particular dot was admitted. The actual draw
observers and visibility checks provide that proof. The timed counters are
test-only instrumentation; they are absent from production source.

All source phases recorded zero forbidden requests, missing code or page errors.
Only GET/HEAD routes were admitted. No survey write, session start, physical
command or screenshot occurred. Candidate authored pack hashes remained unchanged. Every
browser and local server was closed after its phase.

## Bounded performance evidence

All three phases used Edge `155.0.4283.62`, the same RTX 4090 through ANGLE/D3D11,
1366 × 768 viewport, 1026 × 716 canvas, DPR 1, `maxFidelity`, and `snapHome(0)`.
Each mode/condition warmed for 120 renderer ticks, matching the existing stats
ring, before collecting four production `stats` events. Warmup observers ended
before sampling. Two successive pairs form repeated window medians. No
screenshot timing is treated as frame cost and no fixed regression tolerance
was invented. Full-precision raw samples remain in the receipts.

The first comparison isolates the new empty engine branch, using source-pinned
baseline engines before/after the final candidate. Values below are milliseconds
and are rounded only for readability.

| Mode/metric | Public baseline window medians | Candidate empty window medians | Median delta |
| --- | --- | --- | ---: |
| Cloud frame p95 | 6.100, 6.300, 6.500, 6.350 | 6.100, 6.100, 6.100, 6.100 | −0.225 |
| Cloud CPU render p95 | 1.050, 0.900, 1.000, 1.000 | 1.100, 0.950, 1.000, 1.000 | 0.000 |
| Photo frame p95 | 6.100, 6.100, 6.500, 6.300 | 6.100, 6.100, 6.100, 6.100 | −0.100 |
| Photo CPU render p95 | 1.200, 1.100, 1.150, 1.000 | 1.200, 1.150, 1.100, 1.150 | +0.025 |
| Mesh frame p95 | 6.100, 6.100, 6.250, 6.150 | 6.100, 6.100, 6.100, 6.050 | −0.025 |
| Mesh CPU render p95 | 1.100, 1.100, 0.950, 0.900 | 1.100, 1.100, 1.050, 1.100 | +0.075 |

The second comparison measures a real dot plus label in the candidate, bracketed
by that same candidate's empty before/after windows:

| Mode/metric | Empty baseline window medians | Active window medians | Median delta | Baseline spread |
| --- | --- | --- | ---: | ---: |
| Cloud frame p95 | 6.100, 6.100, 6.100, 6.100 | 6.100, 6.100 | 0.000 | 0.000 |
| Cloud CPU render p95 | 1.100, 0.950, 1.000, 1.000 | 1.100, 1.100 | +0.100 | 0.150 |
| Photo frame p95 | 6.100, 6.100, 6.100, 6.100 | 6.150, 6.100 | +0.025 | 0.000 |
| Photo CPU render p95 | 1.200, 1.150, 1.100, 1.150 | 1.200, 1.200 | +0.050 | 0.100 |
| Mesh frame p95 | 6.100, 6.100, 6.100, 6.050 | 6.200, 6.150 | **+0.075** | **0.050** |
| Mesh CPU render p95 | 1.100, 1.100, 1.050, 1.100 | 1.000, 1.050 | −0.075 | 0.050 |

The corrected receipt explicitly says `requires-review`. Photo and Mesh frame
windows extend above their local empty ranges. Some strict range comparisons
also flag sub-rounding floating-point differences or improvements; the raw
values are retained rather than hidden by an invented tolerance. Source-empty
and visible-dot comparisons are separate, and the timed draw counter adds a
small observation cost to the latter. No blanket “unchanged within noise” claim
is made for every metric, device or overlay load.
