---
title: Queue explicit camera refreshes through the existing Victoria adapter
target: backend
type: added
---

Add an internal entry for authorized interactive camera requests, including
known empty rooms. Reuse the adapter's bounded queue, freshness checks, deadlines
and durable submission accounting; interactive work replaces only unsent
background work. Persist accepted request IDs in the existing submission journal
before queueing; duplicates retain their deadlines, unsent work is not replayed
after restart, and uncertain dispatched work stays charged. Authenticated HTTP
integration and live commissioning remain pending.
