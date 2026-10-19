# Moving-label layout correction — APT-F01

The measured moving-label layout defect is corrected. Overall renderer timing
acceptance remains **inconclusive**; F01 remains partial and PR #288 stays draft.
This is authored software evidence, not actual-room, installed or physical acceptance.

## Cause and bounded correction

In the frozen allocation-corrected source, a 480-RAF moving four-label trace
contains 479 `Layout` and 479 `UpdateLayoutTree` events with the leaf's final
`current()` → `currentViewport()` → `viewport()` stack. Label `left`/`top`
writes precede that retained viewport read. A separate heap-only pass reports
488 layouts with labels versus four without. Intrusive profiling attributes
the cause; its sampled CPU/heap values are not unprofiled cost measurements.

The correction fixes label layout origins at zero and writes one pixel-plus-
percentage transform when either projected coordinate changes. Every viewport,
owner, visibility, currentness, clipping, abort and disposal check stays in its
existing location. Engine and picking bytes are unchanged. A callback still
receives its own world vector.

The unit suite is 38/38 green. Before correction, 35/38 pass; the new moving
one/four-anchor cases detect two/eight layout-affecting style writes, and the
coordinate case rejects the old layout origin. Native browser checks measure
rendered center-bottom alignment rather than treating style strings as pixels.
The complete functional browser proof passes all nine stages, including long
literal text at the right edge, correct withdrawal after clipping, and visible
same-node alignment/drawing through native host resize and restore in all three
modes. Receipt: `.tmp/apartment-overlay-bridge-browser/run-1792378710552-candidate/receipt.json`.
Fixed-origin wrapping is viewport-dependent rather than position-dependent;
clipped long text is recorded, not claimed fully visible or legacy-equivalent.

## Unprofiled source comparison

One fixed acquisition completed all 24 slots in 86.788 seconds: old source,
transform source, then old source again, each with static and moving ABBA
conditions. Every slot contains 120 separate washout RAFs and 480 capture RAFs,
with two disjoint production-stat rings. Edge 155 / bundled Playwright 1.62.1,
desktop DPR 1, actual Mesh, all four fixture volumes on and maximum fidelity
remain fixed. No CPU/heap profiler or tracing runs. The owner's preview stayed
open and was not confirmed idle, so no exclusive-GPU claim is made.

Diagnostic CDP counter intervals include IPC, checkpoint and bracketing frames;
they are wider than exactly 480 capture RAFs. Their moving-layout counts are:

| Source | No-label A counts | Four-label B counts |
|---|---|---|
| Old before | 4, 4 | 484, 484 |
| Transform | 4, 4 | 4, 5 |
| Old after | 4, 5 | 483, 484 |

Static layout counts are zero throughout. Moving style recalculation remains
approximately once per frame; this correction removes repeated layout, not all
style work, renderer draws, allocations or CPU/GPU cost.

The strict frame/proxy analysis retains four candidate flags and four empty-
source drift flags. Candidate static/orbit render-proxy ABBA deltas are
`+0.45000001192147465` / `+0.4249999940402631` ms. Static frame-p95 has a
`+9.094947017729282e-13` ms exact-arithmetic flag; moving frame-p95 has a
`-0.024999999999636202` ms improvement flag under the two-sided rule. None is
waived, rounded away or converted into performance clearance. No further
measurement retry is used to select a favorable result.

Two earlier setup-only failures remain. Their new native-bounds check exposed
old `left`/`top` layout quantization: 12/12 observations equal
`floor(projected * 64) / 64`, exceeding the direct 0.01-pixel check in some
coordinates. The new acquisition retains 51 signed legacy geometry flags and
admits only that exact old-source quantization. The transform candidate keeps
the original direct 0.01-pixel check without exception. Raw geometry is now
sealed before assertions. This baseline geometry correction changes no timing
threshold, source order, capture count, output fence or 145-second deadline.

## Source and retained evidence

- Old leaf: `f56bdc8d346923dd92a29892d71615ae0e429f6ffaf1e13c7aa9042c1f4d3c7e`, code `32dee79c168296139db510eeff4ff6d7f677feae`.
- Transform leaf: `ce3b0f7277be3a5a74410f807d7cda1d4dd94374149b0936b5ee7abea35ce0da`.
- Engine: `db26d0fa57cd1c81e808f0b6beb9e40eac0f9b9d94a57cd2a308adbe0d4725b7`; picking: `ffd237472209a55793e17ed414d10686fbbce4a2cf2a4bb4a7c2991e71316e53`.
- Frozen harness/protocol: `57526d956defb2312b99d8b7d977d1e1e6dc20b4a3b30f7952c2612883b74b85` / `47586af11a3938e122979dea368837b98580c296ef50dedbb1615dec50beb753`.
- Analyzer: `a09c264800a2c9195f36d63bc4659a34db67f3b72e3139f818b15cc24b5155d0`; it separately pins the truthful docs-only snapshot HEAD `2de3ba8a` and old code commit `32dee79c`.
- Local receipt and full analysis: `.tmp/apartment-overlay-transform-unprofiled-v2/run-1792378000370/`.
- Historical profiling, failed setup receipts and geometry diagnosis remain under `.tmp/`; no raw trace or household artifact is published.

The focused comparison does not replace full mode, resize, clipping, lifetime,
keyboard or touch regression checks. [Earlier matched results](wave5-overlay-performance-review-20261019.md)
and their unresolved qualifications remain intact. F05/F06 remain contract-only.
