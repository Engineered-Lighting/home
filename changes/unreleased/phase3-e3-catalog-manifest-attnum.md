---
title: Make the Phase 3 E3 Catalog Manifest Insensitive to Dropped-Column Renumbering
target: backend
type: fixed
---

The Phase 3 activation could not grant the binding stage: `apply-grants.sh` refused with `identity finalizer E3 catalog manifest mismatch` on any deployment that had actually run migration `0010`'s `ALTER TABLE ... DROP COLUMN erasure_request_id`. That migration's `upgrade()` is conditional, so a database bootstrapped from the current schema skips the statement entirely and numbers the surviving columns differently from one that performed the migration — and the manifest hashed `pg_attribute.attnum`, along with the `conkey`/`confkey`/`indkey` attribute vectors, so a purely historical DDL difference read as catalog drift. The manifest now keys and orders columns by name and relies on `pg_get_constraintdef()` and `pg_get_indexdef()`, which already pin constraint and index membership textually, so it pins the same schema shape while ignoring physical attribute numbering. The pinned E3 digest moves accordingly.
