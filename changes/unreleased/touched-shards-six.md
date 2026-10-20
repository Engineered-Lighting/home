---
title: Touched-surface CI runs on six shards
target: internal
type: changed
---

The touched-surface contracts job now splits one plan across six workers
instead of three. A PR that touches the Home shell and Apartment view plans
about 2,300 s of harnesses, which no longer fit three 10-minute workers. Each
worker keeps its 10-minute limit, every harness keeps its deadline, and the
aggregate still verifies that the six receipts cover the plan exactly once.
