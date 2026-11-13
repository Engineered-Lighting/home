---
title: Qualify the den camera's registration and apply its measured depth scale
target: internal
type: changed
---

`tools/den-live/register_now.py` is now registration v2, with the method file `REGISTER-LIVE-20261113.md`.
- The owner types two tapes, kept in the private receipt only. A depth check on the take's per-pixel median compares
  the floor and the facing wall with them. It stops on more than 5 points of bend, applies no scale within 1.5%, and
  otherwise sets one den-live depth scale within 0.90-1.10.
- Nine features (six to fit, three checks beside the sofa) are clicked on the take's median depth, behind an edge
  guard and a 36-pair distance check. den-register fits unedited at a 0.15 m gate, then each sofa check must be within
  10 cm.
- A held-out empty-room take is carried into the room through the new registration and compared there with the
  scan's floor and upper walls, each judged on its own: at least 75% within noise, and no 1 m range bin with a median
  residual over 10 cm.
- The receipt becomes `den-live-register-now-2`, adding `depth_scale`, `scale_check`, `sofa_check_errors_m`,
  `held_out` and `qualified`, and the paste ends with a words-only summary block.

The installer, the LA service and `run_local.py` now start only on a receipt that says `qualified: true`, and
`run_local.py` no longer accepts a room model with no receipt. den-live multiplies every depth plane by the receipt's
`depth_scale` before any lift, the figure's depth included, and refuses to start when that scale and the registration's
own take disagree. At 1.0 nothing changes. A registration made before v2 has to be made again.
