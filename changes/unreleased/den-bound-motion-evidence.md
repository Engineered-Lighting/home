---
title: Preserve person-bound Den motion forecasts
target: backend
type: added
---

Den retains its existing tracker's velocity and full uncertainty for short-horizon motion forecasts. Forecasts require current person, camera, calibration and timing receipts, and abstain when those receipts or sufficient observation history are missing.
