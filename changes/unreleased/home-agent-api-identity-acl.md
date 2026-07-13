### Home Agent online identity authority containment

- Reset the online API's identity-schema ACL after every grant replay and
  replaced schema-wide/default DML with exact current-table reads and
  column-level principal-binding writes.
- Removed online API execution of arbitrary-person binding cancellation and
  denied direct People, alias, recognition, privacy-directive, legacy-candidate,
  and source-entity-binding mutations.
- Preserved only the immutable, principal-scoped confirmation receipt INSERT
  required by privacy opt-out and governed descriptor lifecycle workflows.
- Retired the unsafe source-entity-binding route as a no-body/no-database
  `capability_disabled` tombstone until database-enforced principal, person,
  and confirmation-artifact provenance is available.
- Preserved and PostgreSQL-tested authenticated HA request plus isolated
  operator staging under the real runtime roles, while making subject
  confirmation a no-body/no-database tombstone until one atomic kernel can
  create and validate the full authority graph.
