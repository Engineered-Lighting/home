---
title: Bound connected Den context requests and cancellation
target: backend
type: fixed
---

Enforce one request budget across context uploads and journal responses, and
close outstanding local I/O on cancellation or scope invalidation. Slow chunks
cannot renew the deadline; interrupted intents retain their existing unknown
effect and immutable settlement rules.
