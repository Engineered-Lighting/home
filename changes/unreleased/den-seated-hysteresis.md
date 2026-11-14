---
title: The den no longer flips a seated person to standing, and their reading light no longer flickers
target: backend
type: fixed
---

den-live's seated rule (`tools/den-live/seated.py`) and the published posture (`person_fields.posture`) now have hysteresis. Measured on LA with the owner seated, their mid-hip sat on the top edge of the rule's seated band (0.75 m above the floor). Any frame with the hips a little higher ended "seated", and with it the reading light (the quick exit on standing). In 163 s of sitting, Home's posture changed 42 times.
- **Becoming seated** also counts a head below 1.40 m, held and still for 2 s as before. The owner's band-edge hips no longer stop them being seated at all.
- **Staying seated** ends only on clear standing evidence:
  - at once, on one frame with both the head (1.60 m or more) and the mid-hip (0.90 m or more) up, so standing up is as quick as before;
  - otherwise, on two frames in a row with a head at 1.50 m or more, or (with no head) a mid-hip over 0.95 m, or the mid-hip 0.35 m above the knees. A head at seated height (under 1.40 m) outweighs the hips and the knees, so a knee point found at the feet never unseats a reader.

  Hips at the band's edge never end it.
- **Posture** is sitting while seated, standing only on that same evidence (one frame), walking as before, and otherwise unclear.
- The copy of the coordinator's rule (den-core `body.py` at its pin) is unchanged; the hysteresis is on den-live's side of `seated.py`.
