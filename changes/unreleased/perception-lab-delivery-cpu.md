---
title: Reduce Lab delivery CPU overhead without extending evidence freshness
target: backend
type: fixed
---

Use lower-cost lossless image compression for paired worker delivery and reuse
directory-entry metadata during exact storage quota checks. Retain pixel hashes,
storage bounds, source expiry, and durable execution accounting.
