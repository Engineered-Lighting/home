---
title: Repair the Phase 3 identity migration chain
target: backend
type: fixed
---

Revision `0007_phase3_identity_authority` rendered its table DDL from
`app.schema`, which tracks the current shape of each table rather than the shape
that revision installed. Revision `0010` later rewrites the erasure-impacts
table, so `0007` had begun emitting a foreign key to a table `0010` does not
create until three revisions later and the dormant `0007`–`0013` chain could not
apply at all. The table DDL in `0007` is now frozen in the revision itself and no
longer derived from `app.schema`, and the erasure-impacts table is created at the
pre-`0010` shape that `0010` expects, which also makes `0010`'s previously
unreachable predecessor-migration path work as designed.
