# APT-F01 overlay label timing — W1 ABBA receipt (authored scenes)

Status: **protocol pre-registered; no measurement yet.** This section was
committed before any ABBA sample existed. Results are appended below it,
without editing the protocol.

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
