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
