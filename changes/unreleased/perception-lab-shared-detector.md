---
title: Share one detector stage between pose comparison arms
target: backend
type: added
---

Add an explicitly admitted, durable shared detector stage for identical-frame
pose comparisons. Both arms reuse the same proposals and provenance; uncertain
detector completion blocks reuse without rerunning inference.
