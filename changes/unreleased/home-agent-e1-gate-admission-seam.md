---
title: Make the hosted gate drive the real admission writer and the new login
target: backend
type: fixed
---

The identity finalizer's runtime tests all hand-insert their admission row, and
the admission writer's own tests only assert that its expiry literal appears in
the source. Nothing exercised the seam between the two, which is how an
admission that could never be finalized shipped unnoticed.

The E3 runtime suite now has a test that admits through
`app.identity_admission_writer` itself, asserts the stored expiry satisfies the
predicate the finalizer kernel rejects on, and then finalizes. It fails against
the previous writer and passes against the fixed one.

The gate also exercises the new `migration` arm of the identity authority role
ceremony beside the existing `finalizer` and `cutover` arms: dormant login
refused, bounded activation, real login proven, re-expiry, and refusal again.
The registration contract tests run in the same phase.

The gate also now cross-checks its own two lists: every `tests/home_agent/...`
node a phase executes must appear in the build context the gate assembles.
Those lists are maintained independently, and a node added without its file was
only discoverable by running the gate and reading a "file or directory not
found" several minutes in.

Still not covered: `test_phase3_identity_migration_kernel_postgres.py`, which
calls the registration kernel, is in no node list and remains so. Running it
needs a bounded activation window held across the pytest run and a disposable
`0008` database — the one-shot `record_only -> shadow` authorization collides
with the E3 fixture's own run row. That wiring should land before the kernel is
ever called against the live cluster.
