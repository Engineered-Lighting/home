---
title: Select paired detector and pose experiments in Home
target: backend
type: added
---

Add an on-demand YOLO26s plus RTMPose comparison on Victoria and LA, with separate worker reservations, pinned composite manifests, and recorded pose evidence available in replay. Preserve the detector-only comparison; RTMPose uses explicitly identified CPU execution while detection remains on each GPU.
