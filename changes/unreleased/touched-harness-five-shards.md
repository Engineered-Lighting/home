---
title: Touched-surface checks run on five shards
target: internal
type: changed
---

The touched-surface CI job now splits its plan across five workers instead of three. Any change to the Apartment view now plans about 50 suites, which no longer fit three 10-minute jobs. The plan, the suites and their deadlines are unchanged, and the aggregate still requires the five receipts to cover the plan exactly once.
