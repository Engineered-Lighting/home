---
title: Add the Phase 3 erasure-operation foundation
target: backend
type: added
---

- Add a dormant, owner-only Phase 3 erasure-operation source foundation so
  future lineage cleanup can be tied to either an authorized erasure request
  or an imported-person auto-expiry schedule without inventing a principal.
  The schema adds no callable erasure/finalizer writer and leaves production
  pinned to the record-only revision.
