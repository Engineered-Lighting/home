---
title: Add the manifest-only Phase 3 identity migration kernel
target: backend
type: added
---

Phase 3 adds an intentionally non-deployable, manifest-only PostgreSQL kernel
for reviewed legacy identity migrations. The exact migration session can query
content-free capabilities and atomically register a bounded, dense, fully
replay-checked source/decision manifest through functions owned by a separate
NOLOGIN kernel role. Direct table access remains denied, one shadow promotion
can admit only one reviewed run, and downgrade refuses to strand admitted
evidence. A non-callable erasure-impact trigger and matching no-op run-row
updates create an MVCC fence, so a replay waiting on concurrent erasure fails
serialization and a later retry returns the governed erasure block. Clients
retry the whole transaction on `40001`, `40P01`, or `55P03`. Semantic
finalization remains disabled with
`external_payload_verifier_not_implemented`; production Core stays pinned to
migration 0006.
