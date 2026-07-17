### Home Agent Phase 3 semantic verifier foundation

- Added an offline, network-free verifier for canonical reviewed identity
  projection bundles.
- The verifier checks exact JSON bytes, rejects duplicate keys, floats,
  noncanonical Unicode and timestamps, verifies a purpose-scoped Ed25519 review
  signature, and recomputes domain-separated keyed commitments for every
  source, decision, semantic projection, receipt, and aggregate root.
- Typed projection validation permits only canonical People, privacy, aliases,
  Frigate bindings, archived status, and non-authoritative legacy role or
  relationship candidates. Parent facts and authority/cutover fields are not
  part of the output contract.
- Semantic database finalization remains disabled until a separate
  least-privilege finalizer role, atomic PostgreSQL kernel, and row-addressable
  erasure lineage are implemented and proven. The production schema pin remains
  revision `0006a_worker_lease_arbitration`.
