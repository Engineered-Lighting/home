# Escape integration review

Issue: #223 (APT-F04). Base: #284 at
`751d527d68e8a86eba8d43ed059a35e348811ab1`. Unmerged source review on 2026-10-18.

Repair and Den released narrow overlay, caller and editor ownership windows.
Repair additionally approved cancelling armed move/device/Measure UI state.
The accepted Den composition receives narrow patches, never a whole public
Apartment component replacement. No host operations are part of this work.

## Standards

Independent review: **0 unresolved findings; 1 P3 resolved**. A vacuous
preservation assertion was removed; the parent table now accurately proves
dispatch, while the target Cancel test uses actual model state. Repository
policies, change fragment and touched-test registration passed review.

## Spec

The shared dispatcher calls one existing owner operation per press. It
preserves editable/popover ownership, calibration's five-second confirmation,
and the distinct standalone-modal close versus embedded idle pass behavior.
Camera return precedes clearing selection. Banked calibration pairs are
guarded before an existing camera exit can clear them. Unusable focus targets
fall through to an available control or viewport after the React commit.

The independent spec review's body-focus and armed-target-move findings were
resolved. Future proposed-aim discard needs its own producer contract; it is
not implemented by undoing an already committed modeled preview. Geometry
edit-drag rollback remains a separate owner-contract gap. #223 therefore
retains these explicit acceptance limits rather than closing automatically.

## Verification

- 47 source-derived VM tests and 34 mounted React/browser checkpoints. Exact
  sources and per-clause coverage are in `wave2-escape-qa.md`.
- All 12 touched harnesses plus JSX parsing passed, including existing travel,
  aiming, data, 3D, overlay, shell and shared-UI guards.
- Complete authored Home browser acceptance passed all 28 checkpoints and
  10 screenshots in 25.80 seconds locally, with its original 145-second
  deadline and 3cm collision guard. This is not Linux performance evidence.
- Separate private read-only accepted-Den verification passed nine bounded
  checks across four viewport sizes and Cloud/Photo/Mesh. Selection Escape
  restored the viewport after commit; idle Escape retained Apartment;
  popover Escape restored its visible opener. Exact registration remained
  unchanged, with zero session starts, equipment saves or physical commands.

Actual-room evidence and provenance remain private, outside this repository
and CI. Authored software checks, private actual-room presentation, deployed
behavior and physical qualification are separate evidence levels. Hosted
checks must pass on this PR's committed source before it is ready for review.

F05/F06 remain contract preparation only. No merge or deployment is authorized.
