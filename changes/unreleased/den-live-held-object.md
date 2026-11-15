---
title: Find what the Den figure holds, with its label
target: backend
type: added
---

den-live can show what the person holds. A segmentation pass runs beside the tracking detector on the same frame, and the tracking detector stays exactly as it is. A holdable thing (a book, a phone, a cup and so on) whose outline meets measured depth near a wrist becomes its own points in the body frame, with the detector's label, once it's held in two of the last three frames. The points are real depth pixels. Something too small for the segmentation pass, such as a phone in the hand, is still found from depth alone: measured pixels by a still hand that the body doesn't explain. It's sent unnamed, for Home's "Held object" marker. Its model, Ultralytics' YOLO26s-seg (about 22 MB), is pinned by its published sha256. It ships in the release, and the installer puts it beside den-live's other models. Without that exact file the pass stays off, and every frame is exactly as before.
