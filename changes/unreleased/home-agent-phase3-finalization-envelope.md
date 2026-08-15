---
title: Sign the Home Agent Phase 3 finalization envelope
target: internal
type: changed
---

- Replaced the incomplete semantic-finalizer document with a separately signed,
  immutable finalization proposal that remains non-authoritative.
- Added distinct purpose-scoped Ed25519 review and finalization keys plus
  domain-separated HMAC commitments for decisions, receipts, normalized
  lineage, per-person privacy closure, and deterministic auto-expiry effects.
- Added fail-closed same-run person/reference rules, ignored and do-not-track
  handling, unique status/privacy projections, future auto-expiry admission,
  and restart-safe verification of an exact signed replay after expiry.
- Kept PostgreSQL semantic projection, authority cutover, parent confirmation,
  memory, greetings, and physical action disabled; production remains pinned to
  revision `0006a_worker_lease_arbitration` in `record_only` mode.
