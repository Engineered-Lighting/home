---
title: Coordinate bounded detector trials with a scheduled start
target: backend
type: added
---

Add an explicitly armed future-start helper for the authorized paired detector
trial. Reuse the existing health-checked launch helper, preserve receipts, and
stop only the captured service invocation after the agreed window. No retry or
automatic activation is enabled by staging the helper.
