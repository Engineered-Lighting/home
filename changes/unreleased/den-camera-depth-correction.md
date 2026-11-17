---
title: Den camera depth now reads true metres
target: backend
type: fixed
---

The den OAK-D Pro's depth read 9–15% long. That error reached everything that measures metres from it: the
body cloud, the sofa mark and registration. The camera service now applies a measured correction, so the
floor and the facing wall land within about 3% of tape. It applies only while the camera keeps the
calibration it was measured with, and every frame declares whether it was applied. Depth-derived geometry
(registration, the sofa mark) needs re-registering once this release runs.
