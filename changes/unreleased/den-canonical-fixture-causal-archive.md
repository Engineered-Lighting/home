---
title: Retain canonical fixture authority and dispatch causality
target: backend
type: added
---

Add a bounded private sidecar for canonical fixture grants and dispatcher
transactions, preserving their original bodies, receipt clocks and pending
effects. Its independent causal reader refuses missing originals and unresolved
operations, including original bounded-call timing. Existing archive readers
remain unchanged. Full authority policy, independent validation and clock
originals must join the separate worker, room and native verification before
claiming end-to-end replay; this sidecar grants no current or physical authority.
