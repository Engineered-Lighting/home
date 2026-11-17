---
title: Preserve virtual fixture hand control across delayed requests and restarts
target: backend
type: fixed
---

Home and Den Live now order virtual fixture edits by hold and revision, reject stale Resume requests, and recover acknowledged hand holds after a producer restart. Replayed requests preserve their original outcome, and restoring a hold in Home produces no new learning event.
