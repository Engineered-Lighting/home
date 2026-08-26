---
title: Re-pin the Phase 3 activation source
target: internal
type: changed
---

The Phase 3 activation source pin now accepts commit `683e1cf`, which unblocks
the activation at step 17 — registration through the stdin bridge, the finalizer
admission clamped to its run, a deployment-chosen readiness pin, a bounded
activation arm for the migration login, and an explicit role on each authority
ceremony command — together with the PostgreSQL gate run that verified it green
on both jobs. This is a pin-only change: the activation source plan is otherwise
byte-identical, confirmed with `source_plan_matches_accepted_pin_only`.
