---
title: The Den figure moves at the camera's pace
target: backend
type: changed
---

The live figure can now update on every tracked camera frame, about 4 a second, instead of on every other one. A body job could go out no sooner than 0.3 s after the last, but frames come about 0.24 s apart, so half were skipped and the figure moved at 2 a second. The least gap is now 0.2 s. A frame still at the detector still skips its body, and so does a frame where the Qwen ask is waiting for its stills: the ask comes first.
