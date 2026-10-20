---
title: Keep analyzed-frame pose status and timestamps explicit
target: web
type: fixed
---

Explain when an analyzed frame contains no supported person joints instead of showing an empty skeleton layer. When the backend supplies an exact per-arm observation snapshot, preserve that frame's original pose timestamps as newer graph results arrive; keep independent model results and image expiry unchanged.
