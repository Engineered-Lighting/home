---
title: LA Lab preflight accepts two diagnosed backup-attempt failures by exact name
target: backend
type: fixed
---

The LA Lab worker preflight still refuses any new failed systemd unit, but now also recognizes the two superseded, documented off-host backup attempts from 2026-09-28 by exact name, so they no longer block every Lab session. Their failed state is kept as incident history rather than reset.
