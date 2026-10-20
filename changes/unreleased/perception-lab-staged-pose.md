---
title: Connect admitted detector and pose stages in Lab comparisons
target: backend
type: added
---

Add a local detector-to-pose composite with frame-bound crops, common person IDs,
per-stage provenance and a durable parent reservation. Empty detections skip pose;
uncertain failures cannot silently replay a stage or publish completed evidence.
