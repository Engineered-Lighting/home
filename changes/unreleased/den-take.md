---
title: Den take recorder with a written capture-clock check (rung R)
target: internal
type: added
---

`tools/den-take` records single-person den sessions on the owner's PC from the
installed camera's `/frame.npz` bundles, keeping each frame's own capture time,
and checks every take against a method and tolerances written down before any
run (`CHECK-V1.md`), using a phone slate and a two-clock rule. Nothing is
installed or changed on the Jetson, and takes stay on the PC, outside git.
