---
title: Add a recovery path for a finalizer admission spent on an unfinalizable packet
target: backend
type: added
---

`commit_finalizer` writes its admission under a fixed operation id held in the activation journal, and the admissions table keys on that id, so one activation gets exactly one admission. If the packet it was spent on can never finalize — because the finalizer kernel refuses its projections — the run was stranded with no verb that could move it: retirement requires that nothing was registered, and the ceremony's supersession requires an unsigned packet still at `staged`. `phase3_activation_runner.py recover-finalizer-admission` re-mints the admission operation id and archives the stale signing artifacts so a fresh packet can be staged. It writes nothing to the database, advances no step, rewinds nothing, and refuses unless the database proves the spent admission was never consumed or finalized.
