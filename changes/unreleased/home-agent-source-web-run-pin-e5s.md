---
title: Source admission binds the tested web boundary
target: backend
type: changed
---

Phase 3 source receipts now take the accepted web-boundary workflow run from
the same pin-only source manifest as the PostgreSQL authority run, preventing a
stale hard-coded web result from being reused after relevant deployment changes.
