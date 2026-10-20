---
title: Require validated depth provenance for Lab room positions
target: backend
type: fixed
---

Reject room-position publication without a registered depth source matching the
camera, capture epoch, frame, calibration and observation time. Unvalidated depth
or clock mapping cannot become measured spatial evidence.
