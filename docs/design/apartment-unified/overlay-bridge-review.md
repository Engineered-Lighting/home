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
844 on every platform. It retains all nine stages, three actual mode-readiness
and dot-draw checks, races, performance assertions, twelve 120-RAF warm-ups,
the 25-second warm-up deadline and the 145-second global deadline. Bounded
warm-up diagnostics record observed RAFs, renderer frame increments, timing,
visibility, canvas, quality and four recent stats events. The receipt records
its configured profile/viewport and limits functional timings to that viewport.
Hosted verification of the profile correction is still pending.

Run the default functional check with no source-phase override:

```powershell
$env:HOME_APARTMENT_OVERLAY_PROOF_PROFILE = 'functional'
$env:HOME_APARTMENT_OVERLAY_PROOF_PHASE = 'candidate'
node --test tools/test-apartment-overlay-bridge-browser.mjs
```

The desktop comparison remains a separate, explicitly selected profile. Each
phase must use the same desktop profile; baseline phases refuse the default
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
