---
title: Bound one-time identity authority credentials
target: backend
type: added
---

Reviewed identity finalization and cutover operations now activate only their
dedicated nonprivileged database login for a two-minute window, then forcibly
expire it and terminate any residual session. The operator-only ceremony is
accepted-source-bound, permit-gated, private-stdin-only, and exercised against
disposable PostgreSQL before deployment.
