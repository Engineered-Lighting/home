---
title: Preserve person-bound Den motion forecasts
target: backend
type: added
---

Den retains its existing tracker's velocity and full uncertainty for short-horizon shadow motion forecasts, with lossless person, camera, calibration and timing receipts. Forecasts expose estimated source elapsed time as unqualified, abstain on insufficient or inconsistent history, and report clock changes or overlapping capture intervals as numeric counters; they do not establish physical timing or demonstrated error containment.
