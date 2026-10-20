---
title: Add shared-crop Perception Lab pose adapter
target: backend
type: added
---

Add an admitted RTMPose-m Body7 adapter with frame-bound person crops, separate
person skeletons and explicit uncertain joints. Preserve raw pose response scores
without labeling them probabilities, and prevent empty crop sets from triggering
full-frame pose inference. Actual model/runtime builds remain unvalidated.
