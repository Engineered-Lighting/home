---
title: Decode registered depth into conservative surface estimates
target: backend
type: added
---

Add a bounded numerical depth reader with frame, calibration, clock and byte
integrity checks. Surface estimates retain patch extent and sensor uncertainty;
unvalidated, stale, occluded or discontinuous evidence cannot become a precise
room position. Hardware commissioning and live pipeline wiring remain pending.
