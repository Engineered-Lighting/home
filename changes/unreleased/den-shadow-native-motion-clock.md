---
title: Isolate capture timing for experimental walking forecasts
target: backend
type: added
---

Explicit shadow or virtual walking previews can use native capture elapsed time
in the existing tracker, keeping arrival jitter separate from conservative age
and expiry bounds. Ordinary Den startup keeps its current timing behavior;
source discontinuities immediately revoke the experimental history.
