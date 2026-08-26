---
title: Add a recovery verb for an expired Phase 3 activation grant permit
target: backend
type: added
---

The Phase 3 activation grant permit expires four hours after it is armed, but the activation windows span private human confirmations that have no bounded duration, so a run could strand itself with no way to refresh the permit. `phase3_activation_runner.py recover-permit` re-arms it after re-establishing the admitted hosted source and pinning the database to the exact revision the activation journal says it reached. It runs no migration, starts no service, and cannot move a run past a human confirmation.
