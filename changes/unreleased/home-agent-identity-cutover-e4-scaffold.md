---
title: Contain the dormant identity-authority cutover
target: backend
type: added
---

Adds an expired, isolated database role boundary and fail-closed operator
surface for the future E4 identity semantic-authority cutover, including a
non-regenerating recovery path and hosted behavioral gate for its additive
secret lifecycle. Replays now terminate and prove the absence of disposable
cutover sessions, cascade delegated-grant cleanup, database-scope the login,
and reject orphaned or stale credential publications. Production remains
record-only at revision 0006a, with no cutover activation path.
The hosted gate now pins the reviewed dormant E4 post-quarantine catalog and
must still stop at the explicit activation-not-installed boundary.
