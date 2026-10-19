# APT-F01 overlay label timing — W1 ABBA receipt (authored scenes)

Status: **protocol pre-registered in `b702dc08` before any sample existed;
the protocol text below is unchanged. Two acquisitions are appended under
"Results".** On October 19, 2026 the owner accepted the display-bound
result for the M1 timing gate (see "Owner decision"). F01 itself stays open.

This is authored-scene software evidence only. It does not cover actual Den
imagery or registration, installed builds, physical output or touch hardware.
F01 stays partial and #288 stays draft until the owner or Repair accepts a
result.

## Code under test

- Base: #293 head `4a374e78`, plus the #288 test/doc remainder (test-only).
- Overlay leaf `app/src/home-3d/apartment-ui-overlay-bridge.mjs`: sha256
  `36aed31f0dadd17a72e400332f8d3bcb7db8c95bf36fa3205999b74746fca99c`.
- Engine `app/src/home-3d/engine.js`: sha256
  `397576a47ec99e6ed9de83d1f8a44bbc3618f72d9e971055f6a11884ceba0c5a`.
- Picking `app/src/home-3d/picking.js`: sha256
  `ffd237472209a55793e17ed414d10686fbbce4a2cf2a4bb4a7c2991e71316e53`.

The receipt records the served hash of every source file. A run is void if
any of these three hashes differ.

## Protocol

**Harness.** `tools/test-apartment-overlay-bridge-browser.mjs` with
`HOME_APARTMENT_OVERLAY_PROOF_PROFILE=desktop-abba`, using the existing
authored Echo/Victoria packs, real renderer and equipment registration
gateway. The receipt records the browser version, GPU and pacing.

**Conditions:**

- desktop viewport 1366×768, device pixel ratio 1
- `maxFidelity`, maximum quality
- camera at `snapHome(0)`, held static during every window
- all four virtual spotlights displayed
- browser-default pacing

**Scenes:** Cloud, Photo and Mesh (`points`, `splat`, `mesh`) on the authored
Victoria pack.

**Treatments:**

- **A**: labels off. No overlay layer exists.
- **B**: labels on. One overlay layer with four anchors, one at each fixture's
  `display_anchor_m`, labelled with the fixture label. Each anchor draws its
  dot and DOM label.

**Design.** For each scene, 4 blocks in the order A1 B1 B2 A2: 16 windows per
scene, 48 in total.

**Window.**

1. After each treatment switch, wait 120 animation frames (the existing
   warm-up length).
2. The window is the engine's production stats emission whose 120-frame ring
   lies entirely after that warm-up.
3. The harness also records its own 120 animation-frame intervals for the
   same span.

**Primary metrics** (these decide the result):

- **frame-p95:** the engine's `p95` of frame intervals.
- **median frame time:** `1000 / fps`, where the engine's `fps` comes from the
  median frame interval.

**Descriptive metrics** (reported with the same arithmetic; never used to
declare a pass):

- **render-p95:** the engine's `renderP95`, measured from frame start until
  `renderer.render` returns. It includes overlay and UI-layer updates. It is a
  CPU submission proxy, not GPU time.
- **independent animation-frame p95:** the p95 of the harness's own frame
  intervals.

**Noise criterion** (agreed with the Home UI planning session before
measuring):

- **Noise:** in each block, |A1 − A2|. The **AA spread** for a scene and
  metric is the largest |A1 − A2| over its blocks.
- **Validity:** a scene's run is valid for a metric only if its AA spread is
  at most 10% of the median of all its A values.
  - If a scene is invalid on a primary metric, rerun the whole tool once and
    keep both receipts.
  - If it is still invalid, that scene is **inconclusive (noisy)**.
- **Paired delta:** each block's delta is mean(B1, B2) − mean(A1, A2). The
  scene's result uses the median delta over its blocks.
- **No measurable regression:** the absolute median delta is at most the AA
  spread, and no more than 1 block has an absolute delta above 2 × AA spread.
- **Measured difference:** anything beyond that, reported with its sign as a
  cost or an improvement, for the owner or Repair to accept or reject. No
  tolerance is invented, and no pass is declared silently.

**Numeric resolution** (fixed before measuring). Browser timers in this
non-isolated page are coarsened, so engine values sit on a 0.1 ms grid plus
floating-point noise.

- Before analysis, each value is snapped to the nearest 0.1 ms.
- The AA spread is floored at one 0.1 ms quantum, because a spread below the
  clock's resolution cannot be measured.
- The receipt reports every raw value next to its snapped value, and the
  distinct-value set that shows the grid.
- If the raw values do not sit on a 0.1 ms grid, snapping is not applied and
  the receipt says so.

**Known limit, stated before measuring.** If the frame rate is pinned to a
refresh interval, frame-p95 and median frame time can only show label cost
that makes frames miss that interval. Cost below one refresh interval appears
only in the descriptive render-p95. In that case the receipt says the primary
result is display-bound, and makes no sub-frame cost claim.

**Not covered:**

- moving-label cost while orbiting (the camera is static)
- touch hardware, installed builds and actual Den imagery
- reticle and bar overlays, which do not exist yet
- full shell composition with W1's siblings

The protocol has to be re-run on the final top of the W1 stack, as the W1
plan's step 9 requires.

## Results (appended on October 19, 2026, after measuring)

### Summary

- **Primary metrics.** frame-p95 and median frame time show **no measurable
  regression** in Cloud, Photo and Mesh, in both runs. Every scene is
  **display-bound**: headless Chromium paced at 60 Hz, so the A frame-p95
  medians are 16.70–16.80 ms. The bound result therefore shows that four
  labels and dots caused no missed frames at 60 Hz. It says nothing about
  cost below one refresh interval.
- **Descriptive render-p95** (frame start until `renderer.render` returns, a
  CPU submission proxy).
  - The A median is 2.35–3.00 ms.
  - The A/A spread is 0.30–0.70 ms, which is above 10% of the A median in
    every scene. By the protocol, render-p95 is therefore **invalid
    (noisy)**.
  - The median B−A deltas are +0.025 to +0.175 ms, all inside that A/A
    noise. Label cost cannot be told apart from zero at this resolution,
    and no sub-frame cost or clearance is claimed.
- **Descriptive independent animation-frame p95** agrees with frame-p95.
- **Dot draws.** Run 2 witnessed 504–512 dot draws per labels-on window,
  which is one draw per dot per frame. Run 1 checked dot visibility only,
  because `renderer.info` call counts did not change. Both runs are
  reported, and neither was selected over the other.
- **Rerun rule.** Neither run was a rerun under the noise rule: every primary
  metric was valid in run 1. Run 2 exists only to add the draw witness.

### Decision needed (owner and Repair)

This receipt does **not** close F01. Per the Home UI planning record, the
owner and Repair decide F01 acceptance from these results together:

- the display-bound primary result: no missed frames at 60 Hz;
- the descriptive render-p95 figures: noise of 0.30–0.70 ms, with median
  B−A deltas of +0.025 to +0.175 ms inside that noise.

F01 done item 2 ("Renderer frame time unchanged within noise") and the M1 gate
"accepted F01 representative timing" both wait for that decision. Two options
would need a new pre-registered protocol rather than a reinterpretation of
this one:

- a sub-frame cost bound, for example more blocks or a higher-rate display;
- moving-label timing.

### Owner decision (October 19, 2026)

The owner accepted the display-bound F01 result for the M1 timing gate, in
these words: "no missed frames at 60 Hz, render cost within noise, no
sub-frame or moving-label claim."

What the decision covers, and what it leaves open:

- **M1 timing gate.** "Accepted F01 representative timing" is satisfied by
  this receipt, within the limits below.
- **F01 done item 2** ("Renderer frame time unchanged within noise") stays
  unticked until this work is integrated on main.
- **F01 and #288.** F01 stays open, and #288 stays draft.
- **Not claimed.** No sub-frame cost bound and no moving-label timing are
  claimed. Either would need a new pre-registered protocol.
- **Re-run.** W1 step 9 still requires this protocol to be re-run on the
  composed stack top.

### Not covered (as declared before measuring)

- Moving labels while orbiting.
- A higher refresh rate. Headless pacing was 60 Hz; earlier #288 runs used a
  headed browser at about 240 Hz.
- Reticle and bar overlays.
- The composed W1 stack top, where this protocol has to be re-run (W1 step 9).
- Touch hardware, installed builds, and actual Den imagery or registration.
- The owner's desktop may not have been idle during the runs; no exclusive-GPU
  claim is made.
- The runtime is Playwright 1.61.1 with bundled Chromium 149, not the
  Edge/1.62.1 runtime used for #288's earlier runs. The two are not
  comparable.

### Reproduce

From a worktree at the commit under test:

```text
HOME_APARTMENT_OVERLAY_PROOF_PROFILE=desktop-abba node --test tools/test-apartment-overlay-bridge-browser.mjs
```

The criterion is implemented in `tools/apartment-overlay-abba.mjs`, with unit
cases in `tools/test-apartment-performance.mjs`. Receipts are written under
`.tmp/` and are not committed; their SHA-256 values are listed below.

### Run 1 — `.tmp/apartment-overlay-bridge-browser/run-1792431865337-candidate/receipt.json`

- Receipt sha256 `7050f26f46bc75ab7ee622061acef2ac183bc8ee2d788498f36a574100b5b72b`; result `passed`; commit `225b3ada` (B visibility checked; dot draws not yet witnessed).
- Chromium 149.0.7827.55 (Playwright 1.61.1, headless), ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 (0x00002684) Direct3D11 vs_5_0 ps_5_0, D3D11), viewport 1366×768, canvas 1026×716, DPR 1.
- Served pins: leaf `36aed31f`, engine `397576a4`, picking `ffd23747` (match the protocol).
- Treatment B: 4 of 4 labels and dots visible in every window. renderer.info call counts were identical for A and B (76/76 Cloud and Photo, 77/77 Mesh), so this run did not prove the dots were drawn; run 2 adds that witness.

| Scene | Metric | Role | A median ms | AA spread ms | Valid (≤10% of A) | Median B−A ms | Outlier blocks | Result |
|---|---|---|---:|---:|---|---:|---:|---|
| Cloud | frameP95 | primary | 16.75 | 0.10 | yes | -0.025 | 0 | no-measurable-regression |
| Cloud | medianFrame | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Cloud | renderP95 | descriptive | 2.40 | 0.60 | no | +0.175 | 0 | invalid-noisy |
| Cloud | rafP95 | descriptive | 16.75 | 0.10 | yes | -0.025 | 0 | no-measurable-regression |
| Photo | frameP95 | primary | 16.80 | 0.10 | yes | -0.025 | 0 | no-measurable-regression |
| Photo | medianFrame | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Photo | renderP95 | descriptive | 2.60 | 0.70 | no | +0.125 | 0 | invalid-noisy |
| Photo | rafP95 | descriptive | 16.80 | 0.10 | yes | -0.025 | 0 | no-measurable-regression |
| Mesh | frameP95 | primary | 16.75 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Mesh | medianFrame | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Mesh | renderP95 | descriptive | 2.60 | 0.30 | no | +0.050 | 0 | invalid-noisy |
| Mesh | rafP95 | descriptive | 16.75 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |

Display-bound (A frame-p95 within 0.2 ms of a refresh interval): Cloud yes, Photo yes, Mesh yes.

Snapped per-block values, A1 B1 B2 A2:

- Cloud frameP95: 16.7 16.8 16.8 16.7; 16.8 16.7 16.7 16.7; 16.8 16.7 16.8 16.8; 16.8 16.7 16.8 16.7
- Cloud renderP95: 2.9 3.2 2.5 2.3; 2.0 3.2 2.4 2.4; 2.3 2.6 2.5 2.7; 2.4 2.7 2.6 2.7
- Photo frameP95: 16.8 16.7 16.7 16.7; 16.7 16.7 16.7 16.8; 16.7 16.8 16.7 16.8; 16.8 16.8 16.8 16.8
- Photo renderP95: 2.6 2.9 2.6 2.6; 2.4 2.7 2.8 3.1; 2.7 2.7 2.8 2.6; 2.4 2.6 2.8 2.6
- Mesh frameP95: 16.8 16.8 16.8 16.8; 16.8 16.7 16.8 16.7; 16.7 16.8 16.7 16.7; 16.8 16.7 16.8 16.7
- Mesh renderP95: 2.6 2.4 2.4 2.6; 2.8 2.8 2.7 2.6; 2.4 2.6 2.5 2.5; 2.3 2.6 2.4 2.6

### Run 2 — `.tmp/apartment-overlay-bridge-browser/run-1792432167567-candidate/receipt.json`

- Receipt sha256 `05464773ce0f4b9052fb8119f0c03f5ba16de8c56eba571b527e24802d4ad20d`; result `passed`; commit `c5467692` (B dot draws witnessed on every window frame).
- Chromium 149.0.7827.55 (Playwright 1.61.1, headless), ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 (0x00002684) Direct3D11 vs_5_0 ps_5_0, D3D11), viewport 1366×768, canvas 1026×716, DPR 1.
- Served pins: leaf `36aed31f`, engine `397576a4`, picking `ffd23747` (match the protocol).
- Treatment B: 4 of 4 labels and dots visible in every window; 504–512 witnessed dot draws per window.

| Scene | Metric | Role | A median ms | AA spread ms | Valid (≤10% of A) | Median B−A ms | Outlier blocks | Result |
|---|---|---|---:|---:|---|---:|---:|---|
| Cloud | frameP95 | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Cloud | medianFrame | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Cloud | renderP95 | descriptive | 2.35 | 0.30 | no | +0.175 | 0 | invalid-noisy |
| Cloud | rafP95 | descriptive | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Photo | frameP95 | primary | 16.80 | 0.10 | yes | -0.025 | 0 | no-measurable-regression |
| Photo | medianFrame | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Photo | renderP95 | descriptive | 2.95 | 0.40 | no | +0.050 | 0 | invalid-noisy |
| Photo | rafP95 | descriptive | 16.80 | 0.10 | yes | -0.025 | 0 | no-measurable-regression |
| Mesh | frameP95 | primary | 16.75 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Mesh | medianFrame | primary | 16.70 | 0.10 | yes | +0.000 | 0 | no-measurable-regression |
| Mesh | renderP95 | descriptive | 3.00 | 0.60 | no | +0.025 | 0 | invalid-noisy |
| Mesh | rafP95 | descriptive | 16.70 | 0.10 | yes | +0.025 | 0 | no-measurable-regression |

Display-bound (A frame-p95 within 0.2 ms of a refresh interval): Cloud yes, Photo yes, Mesh yes.

Snapped per-block values, A1 B1 B2 A2:

- Cloud frameP95: 16.7 16.7 16.7 16.7; 16.7 16.8 16.7 16.8; 16.7 16.7 16.7 16.8; 16.7 16.8 16.7 16.8
- Cloud renderP95: 2.4 2.6 2.4 2.3; 2.3 2.1 2.2 2.2; 2.2 2.6 2.6 2.5; 2.5 2.6 2.9 2.6
- Photo frameP95: 16.7 16.7 16.8 16.7; 16.8 16.7 16.7 16.7; 16.8 16.7 16.8 16.8; 16.8 16.8 16.8 16.8
- Photo renderP95: 3.3 3.4 3.1 3.3; 3.2 3.2 2.9 2.9; 2.9 3.7 3.3 2.9; 3.0 2.8 3.0 2.6
- Mesh frameP95: 16.7 16.7 16.7 16.7; 16.8 16.7 16.7 16.8; 16.7 16.8 16.8 16.7; 16.8 16.8 16.8 16.8
- Mesh renderP95: 2.8 2.7 2.9 3.0; 2.7 3.1 2.8 3.0; 2.9 3.2 3.4 3.5; 3.4 3.3 3.2 3.2

