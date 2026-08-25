---
title: Re-pin the Phase 3 activation source
target: internal
type: changed
---

The Phase 3 activation source pin now accepts commit `7af3946`, which carries
both the repaired revision `0007` table DDL and the order-insensitive revision
`0011` catalog contract, together with the PostgreSQL gate run that verified
them. This is a pin-only change: the activation source plan is otherwise
byte-identical, confirmed with `source_plan_matches_accepted_pin_only`.
