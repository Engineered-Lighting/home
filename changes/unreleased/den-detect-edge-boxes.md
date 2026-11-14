---
title: Keep a Den frame when one detection box strays past its edge
target: backend
type: fixed
---

den-live no longer drops a whole camera frame because the detector put one box a fraction of a pixel past the frame's edge, for example around someone standing in a doorway at the edge of the picture. A frame was also dropped for a box with no area, a repeated id, an out-of-range score, or more than 64 boxes. Boxes are now clipped into the frame, and the few that can't be used are dropped, keeping people first. About one frame in sixty was lost this way, and each lost frame briefly emptied the live view. When a frame's row is still refused, the detector's log now says why in den-observe's own words.
