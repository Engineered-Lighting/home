---
title: Add pinned vLLM engine and worker cleanup hooks
target: backend
type: added
---

Add guarded engine utilities that require durable registration before dispatch
and record completion only after scheduler cleanup and a worker synchronization
acknowledgment. Unsupported modes stay closed. API-server wiring and live
validation remain required; no model service was changed.
