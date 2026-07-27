---
title: Add the dormant Phase 3 identity finalizer
target: backend
type: added
---

Add an admission-bound, database-only identity finalizer and shared erasure
write fence behind the expired operator login. Production remains pinned to
revision `0006a` in record-only mode while hosted PostgreSQL gates validate the
new dormant revision.
