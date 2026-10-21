---
title: Refuse malformed Den monitor state and missing restart evidence
target: backend
type: fixed
---

Malformed persisted monitoring fields now latch the conservative stop instead of aborting monitoring; the rejected state is retained for diagnosis. Soak admission requires valid restart counters in every service sample.
