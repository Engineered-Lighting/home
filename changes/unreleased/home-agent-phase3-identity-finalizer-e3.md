---
title: Add the dormant Phase 3 identity finalizer
target: backend
type: added
---

Add an admission-bound, database-only identity finalizer and shared erasure
write fence behind the expired operator login. Production remains pinned to
revision `0006a` in record-only mode while hosted PostgreSQL gates validate the
new dormant revision. Grant replay also pins the catalog manifest digest
reproduced by two independent runs of the immutable hosted gate. The finalizer
kernel receives one lock-only `expires_at` column privilege so PostgreSQL can
perform its reviewed migration-run `FOR SHARE`; a session-bound RLS policy with
an always-false write check prevents that privilege from mutating the run.
The verifier's exact legacy-role key manifest now uses the same lexical order
as PostgreSQL's ordered key scan, allowing valid reviewed role candidates to
reach the atomic projection boundary while still rejecting extra or missing
fields.
The admission-consumption update now qualifies its target columns explicitly,
preventing PostgreSQL from confusing the admission argument with the stored
admission identifier at the final atomic commit boundary.
