---
title: Require actual Qwen workload during the occupied Den soak
target: backend
type: fixed
---

Occupied recording sessions and rolls alone no longer qualify the Den soak.
The authenticated sampler also requires advancing, recorded Qwen
completions from the commissioned model throughout the same occupied hour.
Stalls, restarts, back-off and model changes break that span. Read-only workload
status contains no frames or inferred facts and grants no lighting authority.
Receipt version 4 prevents older roll-only results from being reused.
