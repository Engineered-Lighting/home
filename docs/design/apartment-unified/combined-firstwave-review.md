# Combined first-wave review

Reviewed on 2026-10-18 against `b0230dc15034942584836877981d22a6c94da17b`.
The candidate combines #282 at `6ca3f6ad` and #283 at `06c9e502`, followed
by the focus-outline correction recorded below. #281 at `42b38d96` was
reviewed separately as contract preparation only. These changes are unmerged.

## Standards

Independent source review: **0 findings**. The combined product delta follows
AGENTS.md, CLAUDE.md and changes/README.md. The shared UI leaf and narrow
consumers retain guarded actions, source-owned status words and change notes.
F05/F06 describe proposed interfaces; they activate no producer or consumer.

## Spec

Independent source review: **1 P3 finding**, resolved in this candidate.
Toast buttons overrode the global focus outline with a 2px ice outline.
Removing that override restores the documented 1px blue outline. A real
browser computed-style assertion failed at 2px before the correction and
passes for width, style and blue token color in both themes afterward.
No blocking functional, authority or privacy findings were identified.

## Combined authored verification

- Shared UI: 11 deterministic tests; real-browser badge contrast minimum
  4.521:1 across 768 samples; native toast actions and exact focus outline.
- Chat: 9 unread-state tests and 62 source-derived mounted React/browser
  checks. Hydration and streaming revisions are not new unread arrivals.
- Existing overlay, 33 aiming and 174 3D contract tests passed.
- Complete authored Home shell: 28 checkpoints, 10 screenshots, unchanged
  145-second acceptance deadline and 3cm collision guard. Local Windows run
  completed in 27.75 seconds. This is not a Linux performance claim.
- The #283 pinned full Chromium Linux correction retains all assertions and
  graphics/provenance checks; its unchanged head passed hosted acceptance
  twice (71.03s and 126.89s). This candidate needs its own hosted checks.

Authored component screenshots contain no household room assets. Runtime
receipts remain local under `.tmp`; hosted acceptance produces its own
authored receipt from this branch's actual source.

## Separate private actual-room verification

A private read-only composition applies only the reviewed UI deltas to the
accepted Den source. Cloud, Photo and Mesh loaded; four saved fixture entries,
selection and Controls/Chat navigation remained available. Desktop, tablet
and phone layouts were checked. The exact saved registration remained
unchanged. The preview started no sessions and performed no equipment saves
or physical commands. Private room images, coordinates, registration and
source provenance are retained outside this repository and CI.

This verifies bounded browser presentation. It does not establish deployed
behavior, live perception, continuous operation or physical qualification.

## Remaining scope

This integration candidate retains implementation credit without closing
whole tickets. F02's wider informative-text/theme audit and F03's remaining
primitive accessibility work remain partial; no gesture Undo producer was
invented. #223 Escape/focus is the next separate source change. F05/F06
remain contract-only. No merge, deployment or host operation is authorized.

The accepted Den component must receive narrow patches; copying this public
Apartment component wholesale would overwrite separately owned work.
