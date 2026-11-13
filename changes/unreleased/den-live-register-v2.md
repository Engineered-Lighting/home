---
title: Qualify the den camera's registration and apply its measured depth scale
target: internal
type: changed
---

`tools/den-live/register_now.py` is now registration v2, with the method file `REGISTER-LIVE-20261113.md`.
- The owner types two tapes, kept privately only, beside the take and in the receipt. A depth check on the take's
  per-pixel median compares the floor and the facing wall with them. It stops on more than 5 points of bend, applies
  no scale within 1.5%, and otherwise sets one den-live depth scale within 0.90-1.10.
- Nine features (six to fit, three checks beside the sofa) are clicked on the take's median depth, behind an edge
  guard and a 36-pair distance check.
- The gate is now the method file's `gate_m`, 0.15 m, where v1's was 0.10 m. den-register fits unedited at it, and the
  distance check stops only on a feature past it; one past 6 cm but within it is only noted. Then each sofa check,
  c1-c3, must be within 0.10 m.
- `--again` keeps the newest whole take recorded with this method file, or one a v2 receipt names, with its tapes,
  clicks and picks. A take stopped part way keeps nothing, and a v1 take or its clicks never come back. A take with
  fewer than 10 frames (a slow link) is refused and never counts as whole, so `--again` stops on it rather than
  measure, click and fit on it.
- A held-out empty-room take is carried into the room through the new registration and compared there with the
  scan's floor and upper walls, each judged on its own: at least 75% within noise, and no 1 m range bin with a median
  residual over 10 cm.
- The receipt becomes `den-live-register-now-2`, adding `depth_scale`, `scale_check`, `sofa_check_errors_m`,
  `held_out` and `qualified`, and the paste ends with a words-only summary block.

The installer, the LA service and `run_local.py` now start only on a receipt that says `qualified: true`, and
`run_local.py` no longer accepts a room model with no receipt. den-live multiplies every depth plane by the receipt's
`depth_scale` before any lift, the figure's depth included, and refuses to start when that scale and the registration's
own take disagree. At 1.0 nothing changes. A registration made before v2 has to be made again.
