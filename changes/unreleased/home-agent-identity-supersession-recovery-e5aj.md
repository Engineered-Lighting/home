---
title: Governed recovery for expired unsigned People review packets
target: backend
type: added
---

Add a supersede-expired identity signing phase that retires an expired,
entirely unsigned staged People packet behind fail-closed preconditions, an
append-only content-free receipt, and a byte-exact archive, plus an
activation-runner rebind-source command that rebinds the paused runner to a
newly hosted-accepted source pin through a digest-chained receipt without
touching credentials. The review ceremony now refuses to prompt when the
authorization window has lapsed and stage reports the packet expiry.
