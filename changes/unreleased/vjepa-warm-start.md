---
title: Faster LA V-JEPA lease start, pooled worker connection and a thermal rung
target: backend
type: changed
---

The LA V-JEPA supervisor now runs its full kernel-journal fault scan once at start instead of inside the first lease, and forwards clips to the worker over one pooled keep-alive loopback connection. It refuses new clips with the typed reason `thermal_shed` while the GPU is at or above 74 °C (configurable), which Victoria counts as a skip.
