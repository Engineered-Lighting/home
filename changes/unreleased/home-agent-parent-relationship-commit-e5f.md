---
title: Add atomic parent relationship authority commit
target: backend
type: added
---

Adds the dormant E5f PostgreSQL commit boundary for turning one authenticated,
reviewed two-parent preview into two explicit `parent_of` facts, their
provenance, a governed memory transaction, and a normalized authority receipt
atomically. The hosted PostgreSQL gate now races independent committers and
verifies exact replay while production remains pinned to record-only revision
`0006a`.
