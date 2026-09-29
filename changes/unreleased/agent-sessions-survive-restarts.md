---
title: Keep linked Home Agent sessions across BFF restarts
target: backend
type: changed
---

Restarting the Home Agent BFF or victoria-link no longer ends account-linking
use for sessions that were already linked, so the owner does not have to sign
out and back in after every deploy. A session restored without a linked record
(for example from a backup taken before linking) still needs a new sign-in
before it can link. Trade-off accepted by the owner: a logout that failed to
persist is not replayed after a restart.
