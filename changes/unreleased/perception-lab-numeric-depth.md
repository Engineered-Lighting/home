---
title: Accept recorded uint16 depth arrays in robust sampling
target: backend
type: fixed
---

Handle NumPy numerical depth values without treating them as invalid pixels. Preserve mask, discontinuity and support checks, reject boolean pixels, and refuse invalid sampling thresholds.
