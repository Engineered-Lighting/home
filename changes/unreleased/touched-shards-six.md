---
title: Touched-surface checks run on six shards
target: internal
type: changed
---

The touched-surface CI job now splits its plan across six workers instead of
five. A change that touches both the Home shell and the Apartment view plans
about 75 suites (about 2,300 s measured), which needs about 390 s per worker
on six shards rather than about 470 s on five. Each worker keeps its
10-minute limit, every suite keeps its deadline, and the aggregate still
requires the six receipts to cover the plan exactly once.
