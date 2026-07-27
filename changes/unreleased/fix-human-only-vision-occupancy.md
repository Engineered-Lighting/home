---
title: Fix human-only vision occupancy
target: web
type: fixed
---

The vision header now reports cameras as occupied only when explicit human/person labels are present, instead of counting generic Frigate object detections.
