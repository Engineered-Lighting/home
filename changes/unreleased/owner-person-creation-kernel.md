---
title: Let the owner add a person, with their privacy state decided in the same act
target: backend
type: added
---

The legacy per-item People import was retired and its store method left
orphaned, so there has been no way to add a person at all. Reading what that
method did explains why it was not simply re-exposed: it inserted a row and
stopped. A person could exist with no auditable provenance and no privacy state
decided, leaving how the system may treat them to whoever wrote the next row.

This kernel refuses that shape. Creating a person and establishing their privacy
closure are one transaction or neither: exactly one status, written as a literal
so nobody can be created already erased; provenance as an owner attestation
artifact rather than a legacy source reference, so an owner-created person can
never masquerade as a reviewed import that had a verifier this path does not;
and an optional initial directive whose schedule is written alongside it, because
an auto-expiring person with no expiry never expires.

It creates no principal and no binding. Being known to the household is not the
same as having an account, and conflating the two is how someone ends up with
authority nobody granted.

The kernel role held no privilege on the artifact registry — verified at column
level, since the table-level check reports the same answer for roles that do
hold column grants. The grant added here is deliberate, scoped to the columns
written, and revoked on downgrade so the widening cannot outlive the feature.
