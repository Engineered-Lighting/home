---
title: Gate reviewed identity migration apply mode
target: backend
type: changed
---

Require the reviewed legacy Identity Store migration to verify Core's exact
authenticated shadow rollout contract before collecting item confirmations or
issuing writes, while preserving the offline counts-only review path.
