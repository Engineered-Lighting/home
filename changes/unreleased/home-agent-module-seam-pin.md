---
title: Pin the ceremony module-seam gate coverage
target: backend
type: changed
---

Advances the activation source pin to the commit that runs step 17's
registration kernel and its three production modules against real kernels in
the hosted gate.

Steps 25, 30 and 32 call `_require_trusted_source`, which refuses unless the
host checkout matches `ACCEPTED_COMMIT` across all 67 activation paths. The
gate runner is one of those paths, so the coverage added in the two preceding
changes cannot reach the ceremony until the pin moves with it.

Only the pin advances here. No activation-path content changes, so the
`migration_tool_bundle_digest` and `core_schema_digest` the signing bundle is
sealed against are untouched.
