---
title: Re-pin the Phase 3 activation source
target: internal
type: changed
---

The Phase 3 activation source pin now accepts commit `06fd4ac`, which carries
the repaired revision `0007` table DDL, together with the PostgreSQL gate run
that verified it. This is a pin-only change: the activation source plan is
otherwise byte-identical, confirmed with `source_plan_matches_accepted_pin_only`.
