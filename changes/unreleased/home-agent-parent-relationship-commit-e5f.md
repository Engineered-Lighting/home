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

Accepted by the isolated PostgreSQL 17 authority gate in GitHub Actions run
`30381852075`. The gate passed the E5a/E5b catalogs, E5c adapter, E5d
foundation, E5e staging, E5f atomic commit, secret lifecycle, rollback, grant
replay, and labeled cleanup contracts.
