# Scoped renderer bridge review

Related to APT-F01 / #220, against `557164592f916cc4f93b79214f560d24d6f797b6`.
The source is a bounded presentation bridge; it does not implement R01 fixture
selection, room authority, physical output or F05/F06 contracts.

## Independent review

Standards: **0 findings**. Spec: **0 unresolved functional/evidence findings**,
with two initial P2 evidence findings resolved before freezing the candidate.

The initial browser proof established projected label movement but did not
prove this dot was actually drawn or exclude display-mode fallback. The
corrected proof observes real dot draws during orbit and every timed window,
and records loaded Cloud points, visible Photo splats and the display Mesh
without collision fallback. Aggregate draw counters alone are not used as
evidence for admission of an individual dot. Earlier provisional timing
summaries are superseded explicitly in the QA report.

## Verification

- 24 focused leaf cases passed with real Three math and canonical picking.
- Nine authored renderer stages passed. Exact source pins and chronological
  baseline/candidate receipts are in `wave4-overlay-bridge-qa.md`.
- Six mapped harnesses plus JSX and engine/picking ESM syntax checks passed.
- The retained full authored shell proof passed all 28 checkpoints and 10
  screenshots in 25.61 seconds, within its unchanged 145-second deadline;
  receipt `.tmp/apartment-home-shell-browser/run-1792364517024/receipt.json`.
- A separate private composition applied only the four approved engine
  insertion blocks and read-only getter over the accepted renderer. Removing
  those insertions reproduces the accepted engine bytes exactly, preserving
  its separately owned desk-picker behavior. Four temporary anchors used the
  exact saved socket positions, with dot drawing and label alignment verified
  across three actual display modes and four viewport sizes. Paused abort
  withdrew the anchors synchronously. Registration stayed unchanged; commands,
  equipment saves, sessions and page errors were all zero. Its private server
  and browser were closed. No private imagery, coordinates, registration,
  source pins or receipt contents are published here.

## Remaining acceptance

The first hosted run reached its initial site wait before the application had
mounted. The authored receipt had no room-asset reads, missing code, route
violations or page errors. A controlled 16-second application-entry delay
reproduced that exact premature 15-second wait failure with no shell, site API
or engine. The harness now asserts the existing full-shell proof's separate
45-second shell-readiness precondition before the unchanged 15-second site
waits. The global deadline remains 145 seconds, and every functional assertion
is retained. The same delayed startup then passed all nine stages in 44.85
seconds; the final native run passed in 29.33 seconds, receipt
`.tmp/apartment-overlay-bridge-browser/run-1792365261595-candidate/receipt.json`.
Targeted startup phase/response diagnostics remain; the artificial delay was
removed. Slow cold compilation as the original Linux trigger is inferred, not
proven by its older receipt. This test-only correction changes no production
source pin and makes no new paired-performance acceptance claim.

The next hosted run, `39175767856` at `c5760653`, reached the selected Victoria
engine in 18.67 seconds and passed the first two functional stages. It then
missed the unchanged 25-second, 120-RAF warm-up deadline in Cloud, before an
active overlay or performance sample existed. Its 1366 by 768 viewport produced
a 1026 by 716 canvas through Chromium/SwiftShader at DPR 1. The context remained
available; forbidden requests, missing code and page errors were zero.

A controlled Windows Edge probe forced the same SwiftShader graphics backend
without installing anything or changing production source. At the original
desktop size, all nine stages passed in 144.73 seconds, with 120-RAF warm-ups
taking 8.70 to 10.54 seconds. The single-variable 390 by 844 product viewport
produced a 390 by 370 canvas and passed those same stages in 76.68 seconds;
warm-ups took 3.71 to 4.79 seconds. Both runs remained visible, at
`maxFidelity`/`maximum`, and recorded 240 to 265 renderer frame increments per
120 observed RAFs. Neither showed stalled RAFs or a lost context. Receipts:

- `.tmp/apartment-overlay-bridge-diagnosis/run-1792365806659-candidate/receipt.json`
- `.tmp/apartment-overlay-bridge-diagnosis/run-1792365973492-candidate/receipt.json`

This supports sustained software-rendering throughput as the cause of the
resource sensitivity. The faster workstation did not reproduce the hosted
25-second failure, so the exact hosted slowdown remains an inference. Neither
probe clears desktop performance acceptance; both report `requires-review`.

The test now defaults to the explicit `functional` resource profile at 390 by
844 on every platform, with browser `deviceScaleFactor: 0.5`. It retains all nine stages, three actual mode-readiness
and dot-draw checks, races, performance assertions, twelve 120-RAF warm-ups,
the 25-second warm-up deadline and the 145-second global deadline. Bounded
warm-up diagnostics record observed RAFs, renderer frame increments, timing,
visibility, canvas, quality and four recent stats events. The receipt records
its configured profile/viewport/device scale and limits functional timings to
those resource conditions. The explicit desktop benchmark remains DPR 1.

Hosted run `39176891782` at `3f477072` confirmed that the phone viewport alone
was insufficient. At DPR 1, its first Cloud warm-up completed 120 RAFs and 240
renderer frame increments in 24.055 seconds. The next reached 118 RAFs and 236
renderer frames before the unchanged 25-second deadline. It remained visible
at `maxFidelity`/`maximum`, with zero forbidden requests, missing code or page
errors. Twelve warm-ups at that cadence cannot fit the global budget. The
equipment depth pass uses the drawing-buffer dimensions capped at 1024, rather
than a fixed 1024 texture, so lowering browser DPR reduces both its pixels and
the main framebuffer's pixels without changing room assets or geometry.

The final bounded workstation probes kept the 390 by 844 CSS viewport and all
assertions/deadlines. Only the browser backend or browser device scale changed:

| SwiftShader mode | DPR | Canvas | Nine-stage duration | Receipt suffix |
| --- | ---: | --- | ---: | --- |
| GLES driver, unchanged backend | 0.5 | 195 by 185 | 48.58 s | `run-1792366882372-candidate` |
| WebGL-only fallback | 1 | 390 by 370 | 66.96 s | `run-1792366980475-candidate` |
| WebGL-only fallback | 0.5 | 195 by 185 | 48.24 s | `run-1792367063563-candidate` |

Each receipt is under `.tmp/apartment-overlay-bridge-diagnosis/<suffix>/receipt.json`.
All three retained twelve 120-RAF warm-ups, maximum fidelity, actual mode
readiness, dot draws and lifecycle races. The final run recorded 240 to 278
renderer frame increments per warm-up; its GPU was ANGLE/Vulkan SwiftShader,
with unmasked vendor `Google Inc. (Google)`. Both fallback probes recorded the
actual WebGL vendor/renderer. The combined run does not establish a further
improvement over DPR 0.5 alone, and workstation timing cannot guarantee hosted
headroom.

Linux now selects the documented
[WebGL-only SwiftShader fallback](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/gpu/swiftshader.md)
with `--use-gl=angle --use-angle=swiftshader-webgl --enable-unsafe-swiftshader`.
Windows retains its existing D3D11 choice. The functional profile changes only
the browser's device scale; it does not change renderer code or its quality
governor. The test asserts the configured CSS viewport, DPR and backing canvas
dimensions, and checks DPR plus `maxFidelity`/`maximum` in every sampled window.
It also asserts nine stages and twelve completed 120-RAF warm-ups. The desktop
benchmark explicitly fixes DPR 1 for all three source phases.

This is reduced-resolution functional capacity evidence. `onBeforeRender`
establishes draw admission, not full-resolution pixel quality. Historical
desktop noise measurements remain qualified, performance remains
`requires-review`, and hosted verification of this correction is pending.
The exact official harness then passed all nine stages in 47.03 seconds with
its retained Windows D3D11 backend, functional DPR 0.5 and a 195 by 185 canvas;
all twelve warm-ups recorded 120 RAFs and maximum quality. Its receipt is
`.tmp/apartment-overlay-bridge-browser/run-1792367181807-candidate/receipt.json`.
Forbidden requests, missing code and page errors were zero. This verifies the
final local harness bytes and does not substitute for the next hosted run.

Hosted run `39177996939` at `c2e4e902` subsequently exhausted the unchanged
145-second global deadline. Victoria was ready at 16.116 seconds. Both initial
stages and all three actual mode/orbit/dot stages passed; Cloud and Photo also
completed their paired timing windows. Mesh reached its orbit check, with at
least ten of twelve warm-ups complete. Cloud ran near 12 FPS and Photo near
10 FPS; CPU-render p95 was about 18 to 20 ms while frame p95 was about 83 to
116 ms. There were no page errors, missing code or forbidden requests. Exact
warm-up durations were unavailable because closing the browser at the global
deadline happened before the old final-only diagnostic read.

The final controlled pacing comparison retained the same SwiftShader backend,
DPR 0.5, viewport, real RAFs, renderer frames and assertions. Adding only
`--disable-frame-rate-limit` passed all nine stages in 48.82 seconds, versus
48.24 seconds in the preceding paced run. That is no material workstation
gain. Adding the GPU completion barrier then passed in 49.57 seconds, with
twelve 120-RAF warm-ups, 240 to 274 renderer frame increments, no context loss
and no forbidden requests, missing code or page errors. Barrier durations
rounded to 0 ms at the browser's timing precision. Receipts:

- `.tmp/apartment-overlay-bridge-diagnosis/run-1792367750485-candidate/receipt.json`
- `.tmp/apartment-overlay-bridge-diagnosis/run-1792367857492-candidate/receipt.json`

The functional profile now selects this unpaced real-RAF execution explicitly.
[Chromium's switch definition](https://chromium.googlesource.com/chromium/src/+/HEAD/components/viz/common/switches.cc)
states that `--disable-frame-rate-limit` removes scheduler begin-frame limits
and implies disabled GPU vsync. After each functional 120-RAF warm-up,
`gl.finish()` completes queued GPU work, with context-loss checks before and
after. Its duration remains inside the existing 25-second budget: elapsed time
is checked again after the blocking call, so a delayed timer cannot produce a
false pass. This establishes completion, not composited pixel quality. The
desktop benchmark retains its browser-default pacing and omits this barrier.
No profile dimension, quality, count or deadline is reduced further.

Node now retains named warm-up start/progress/completion checkpoints and each
completed timing window immediately. A global browser close preserves those
records, including the latest 30-RAF progress checkpoint, instead of erasing
the only warm-up diagnostics. This is a controlled final Linux pacing test;
hosted benefit remains unproven, and performance acceptance remains
`requires-review` with F01 partial and the PR in draft.

The exact updated official harness passed all nine stages in 26.44 seconds on
the retained Windows D3D11 backend, receipt
`.tmp/apartment-overlay-bridge-browser/run-1792368012233-candidate/receipt.json`.
It retained twelve 120-RAF warm-ups, twelve successful GPU completion checks,
maximum quality, nine timing windows and 111 live checkpoints. A separate
throwaway diagnostic deliberately closed the browser after its first warm-up
and then failed as intended. Its Node receipt still contained that completed
120-RAF/GPU warm-up and eleven checkpoints, demonstrating that closure no
longer erases progress:
`.tmp/apartment-overlay-bridge-checkpoint-close/run-1792368055207-candidate/receipt.json`.
That expected diagnostic failure is not functional acceptance. Both browsers
and servers were closed, and the throwaway harnesses were removed. These local
results leave the next hosted pacing result unproven.

Run the default functional check with no source-phase override:

```powershell
$env:HOME_APARTMENT_OVERLAY_PROOF_PROFILE = 'functional'
$env:HOME_APARTMENT_OVERLAY_PROOF_PHASE = 'candidate'
node --test tools/test-apartment-overlay-bridge-browser.mjs
```

The desktop comparison remains a separate, explicitly selected profile. Each
phase must use the same desktop profile at DPR 1; baseline phases refuse the default
functional profile. Review all three receipts together:

```powershell
$env:HOME_APARTMENT_OVERLAY_PROOF_PROFILE = 'desktop-benchmark'
$env:HOME_APARTMENT_OVERLAY_PROOF_PHASE = 'baseline-before'
node --test tools/test-apartment-overlay-bridge-browser.mjs
$env:HOME_APARTMENT_OVERLAY_PROOF_PROFILE = 'desktop-benchmark'
$env:HOME_APARTMENT_OVERLAY_PROOF_PHASE = 'candidate'
node --test tools/test-apartment-overlay-bridge-browser.mjs
$env:HOME_APARTMENT_OVERLAY_PROOF_PROFILE = 'desktop-benchmark'
$env:HOME_APARTMENT_OVERLAY_PROOF_PHASE = 'baseline-after'
node --test tools/test-apartment-overlay-bridge-browser.mjs
Remove-Item Env:HOME_APARTMENT_OVERLAY_PROOF_PROFILE
Remove-Item Env:HOME_APARTMENT_OVERLAY_PROOF_PHASE
```

**F01 remains partial and this PR remains a draft.** The corrected authored
single-dot Mesh frame-p95 median rises 0.075 ms against a 0.050 ms local empty
spread. Photo frame samples also extend beyond their local empty range. The
performance receipt stays `requires-review`; no threshold or assertion was
weakened and no blanket unchanged-within-noise claim is made. Repair requested
this frozen review handoff with that qualification, without further renderer
changes or optimization in this window.

The owner-approved engine/picking window is released. An R01 consumer requires
its own component/input ownership window and representative label-load
acceptance. Authored evidence, private actual-room verification, installed
behavior and physical qualification remain separate. Nothing is merged or
deployed by this work.
