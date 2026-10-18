# Renderer overlay bridge QA — APT-F01

Functional checks passed with real mode-readiness and real dot-draw assertions.
Performance remains qualified: the corrected Mesh frame-p95 median increases
0.075 ms against a 0.050 ms within-run empty spread. Do not mark the entire
frame-cost acceptance clear. F01 remains draft/partial for this acceptance gap.

This is authored software evidence. It loads generated room assets and survey
records, the real renderer and canonical picking. It is not actual-room visual
acceptance, installed-source verification, live perception or physical evidence.
No household pack, coordinates or private registration is published here.

## Sources and receipts

The final candidate's exact source SHA-256 values are:

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
