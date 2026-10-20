---
title: Connect stored detections to shared-crop pose comparisons
target: backend
type: added
---

Resolve person crops from a pinned detector result with frame, artifact, engine
and policy provenance. Both pose arms can consume the same registered crop set;
missing/stale detections and over-budget crowds remain explicit failures instead
of empty or silently truncated results.
