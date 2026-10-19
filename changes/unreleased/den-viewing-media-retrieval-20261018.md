---
title: Recover bounded Den validation image samples before expiry
target: internal
type: fixed
---

The attach-only Den validation observer fetches matching-frame images before retaining metadata, uses bounded ranged reads, and records ordinary media expiry as a missed sample. Authentication, provenance and integrity failures still stop validation; the independent Stop and cleanup deadlines remain unchanged.
