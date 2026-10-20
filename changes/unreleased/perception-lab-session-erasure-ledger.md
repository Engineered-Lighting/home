---
title: Add durable local session erasure groundwork
target: backend
type: added
---

Add a store-level session-erasure operation that removes local labels, events,
jobs and perception ledgers alongside media, retains a minimal tombstone and
recovers interrupted deletion. Sessions with external inference copies remain
blocked until propagation is implemented. API and UI wiring are still pending.
