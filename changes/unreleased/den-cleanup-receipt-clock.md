---
title: Check Den recording cleanup freshness when its status arrives
target: backend
type: fixed
---

The Den monitor now evaluates recording-cleanup freshness at response receipt, avoiding false stops when cleanup finishes during a monitoring pass. The existing 90-second limit and rejection of missing, failed, stale or future evidence remain enforced.
