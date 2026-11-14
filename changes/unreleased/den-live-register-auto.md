---
title: Register the den camera with no clicks, and judge only the floor the room still shows
target: internal
type: added
---

`tools/den-live/register_now.py --auto` registers the den camera to the owner's scan with no click page and no scan
page. Clicking stays as the fallback.
- The tapes, the take, the camera-correction check, the depth check and the median copy are as before. Tilt and height
  then come from the camera's own floor plane, and yaw and position from a coarse-to-fine search over the den's floor
  in pure numpy, refined by point-to-plane IRLS. It takes a few seconds.
- The paste stops in one sentence when the take or the scan shows no floor, or when the best pose isn't clearly better
  than the best pose elsewhere.
- Nine points on stable structure go to den-register, unedited, as clicks and `measured_with: auto` positions. The
  distance check, the fit and the receipt work as before, and each automatic check must be within 0.10 m.
- `--sofa-mark` takes the owner's sofa mark, so that one check lands on fixed structure near the sofa. The receipt's
  `checks_near_sofa` records whether it did. The mark's numbers never print.
- The receipt records `registration: auto` and `checks: auto`, and the summary says
  `Registration: automatic (no clicks).` `--auto --again` reuses the newest whole take and its tapes.
- The held-out take, clicked or automatic, no longer judges floor the room has changed since the scan:
  - floor now covered by something standing more than 0.15 m above the take's own floor plane, which doesn't depend
    on the pose being judged;
  - floor whose point lies outside the den's walkable polygon, holes excluded.

  Both shares go in the receipt and in one summary line. The upper walls are never masked, and a failure says which
  surface it was on.
- The method file gains five numbers for these rules. A pose that's off still fails.
