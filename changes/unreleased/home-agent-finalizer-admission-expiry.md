---
title: Fix the identity finalizer admission that could never be finalized
target: backend
type: fixed
---

The finalizer kernel refuses an admission that outlives its reviewed run
(`0013_identity_finalizer_kernel.py`: `admission.expires_at >
migration_run.expires_at`), and that comparison is not relaxed for an exact
replay. The admission writer stamped every admission `transaction_timestamp() +
interval '15 minutes'`, while the packet compiler stamps the run
`staged + 10 minutes`.

Passing the kernel therefore required the admission to be written at least five
minutes *before* the run was staged, which cannot happen: staging always comes
first. `commit_finalizer` would have failed with
`identity_finalizer_live_run_mismatch` for every possible operator timing.

The admission now expires with its run, whichever comes first. The run-expiry
filter applies only to the insert, never to the replay lookup — the admissions
table requires `expires_at > admitted_at`, so clamping alone would have turned a
legitimate replay against an already-expired run into a constraint violation
instead of returning the admission that already exists.

The gap survived because the only finalizer runtime test hand-inserts its
admission row rather than calling the writer, and the writer's own test asserted
only that the fifteen-minute literal appears in the source. Nothing exercised
the seam between the two. The hosted E1 gate now drives the real writer.

The semantic cutover admission is deliberately unchanged: its kernel compares
only against its own expiry and never against a run.
