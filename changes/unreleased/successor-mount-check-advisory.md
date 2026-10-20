---
title: Camera-session successor no longer blocked by the depth mount check
target: backend
type: changed
---

The den camera-session successor now records the depth-based mount check without requiring it to pass. The owner's landmark-overlay attestation is what confirms the camera is unchanged. Before this, an empty room under different lighting could refuse the successor even though the camera had not moved.
