---
title: Run the identity registration kernel in the hosted gate
target: internal
type: fixed
---

`operations.register_reviewed_identity_migration` is the kernel that writes the
reviewed run row the activation ceremony's `commit_finalizer` step copies its
provenance from, and no gate phase had ever called it. Its test module existed
but sat in no node list, so the only thing standing between a defect there and
a live ceremony was reading.

It could not simply join the E3 node list. A database admits exactly one
`record_only -> shadow` authorization — `rollout_transition_once` is unique on
that pair, and a check constraint forbids any other pair, so the table holds at
most one row for its lifetime. The E3 fixture consumes it, and the migration
caller holds no `DELETE` with which to reclaim it.

So the kernel gets a disposable database of its own, cloned from the E3
database at `0013`, cleared, and seeded with the single reviewed shadow
predecessor the kernel demands. The kernel matches that predecessor on its
authorization id, shadow rule version, policy version and policy digest
together, and the caller has no API that can discover any of them; a contract
test pins all four against the test module's own constants, so a drift shows up
at review time instead of as `identity_migration_predecessor_invalid` from
inside a container. The seed truncates and lets the schema resolve its own
order — that graph turned out to reach eight dependent tables, not the one a
hand-written order would have covered.

The disposable login helper is now parameterised, because the registration
caller needs a longer window than the E4 executors do. It refuses anything
outside one to fourteen minutes, which is the kernel's own fifteen-minute
ceiling rather than merely "short", and the window and the database both unwind
on failure.

The build-context guard is widened to the core image's test nodes. It
previously covered only the host half, so the other half could go missing the
same way — failing inside the container, minutes into a run.
