---
title: Add gated Victoria snapshot and clip primitives
target: backend
type: added
---

Add an authenticated, bounded latest-JPEG camera endpoint that requires a validated
session-bound clock mapping and encodes outside the camera capture loop. Add tested
in-memory H.264 clip selection with hard limits and explicit partial coverage;
no camera deployment, recorder, upload or shared inference is enabled.
