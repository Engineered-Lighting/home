---
title: Find what the Den figure holds, with its label (off until its model is approved)
target: backend
type: added
---

den-live can show what the person holds. A segmentation pass runs beside the tracking detector on the same frame, and the tracking detector stays exactly as it is. A holdable thing (a book, a phone, a cup and so on) whose outline meets measured depth near a wrist becomes its own points in the body frame, with the detector's label, once it's held in two of the last three frames. The points are real depth pixels. The pass stays off, and every frame is exactly as before, until its model file is approved and pinned.
