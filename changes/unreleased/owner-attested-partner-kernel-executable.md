---
title: Make the Owner-Attested Partner Kernel Executable
target: web
type: fixed
---

Recording a partner never worked. `identity.commit_owner_partner_relationship_e5k` could not execute at all, for five independent reasons: migration 0026 recreated it with a changed signature under `CREATE OR REPLACE` and never reassigned its owner, so `SECURITY DEFINER` ran it as the migration runner and its own first guard rejected every call; both the fifteen- and seventeen-argument overloads stayed live, making the adapter's call ambiguous rather than resolving; the kernel role held no schema `USAGE`, no table grant and no column grant anywhere in the database; and row-level security on the receipt ledger tested `session_user` against the owner, so the tables denied their own writer. The last fault also affected owner-attested person creation, which was granted its columns on `privacy.artifact_registry` but no row policy.

The kernel role is a different role from the parent-relationship kernel — partner, not parent — and that one-word difference is why the gap survived review: every privilege check written against the parent role passes. Grants for it now sit in `apply-grants.sh` beside the parent kernel's, because the erasure quarantine block revokes them from every role if a migration issues them instead.
