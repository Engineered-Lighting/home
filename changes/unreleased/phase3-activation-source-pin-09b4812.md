---
title: Re-pin the Phase 3 activation source
target: internal
type: changed
---

The Phase 3 activation source pin now accepts commit `09b4812`, which quotes the
optioned tmpfs mounts so the identity provisioning services can start, together
with the PostgreSQL gate run that verified it. This is a pin-only change: the
activation source plan is otherwise byte-identical, confirmed with
`source_plan_matches_accepted_pin_only`.
