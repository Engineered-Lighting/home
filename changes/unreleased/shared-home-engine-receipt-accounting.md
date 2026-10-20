---
title: Add durable engine receipt accounting for shared inference
target: backend
type: added
---

Add bounded engine registration and cleanup-receipt storage plus gateway lookup
that preserves reservations until a matching durable receipt exists. Reopening
after a crash cannot turn an incomplete request into a completed one. The pinned
vLLM cleanup hook and deployment validation remain required before admission.
