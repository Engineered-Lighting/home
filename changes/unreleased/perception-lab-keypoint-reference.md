---
title: Normalize Keypoint R-CNN reference evidence for Lab comparisons
target: backend
type: added
---

Add the reference output contract for person boxes and COCO-17 skeleton evidence.
Keep raw joint responses distinct from confidence, retain unsupported joints as
uncertain, and prevent skeleton edges from joining different people.
