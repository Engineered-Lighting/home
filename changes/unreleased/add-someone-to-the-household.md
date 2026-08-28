---
title: Add someone to the household from the People tab
target: web
type: added
---

The People tab can add someone. Until now there was no way to do it at all: the
legacy per-item import was retired and never replaced, so the household roster
was whatever the migration left behind.

The card says what adding someone does and does not mean — it records that your
household knows them, and gives them no account and no authority here. Those are
separate things, and conflating them is how a person ends up with permissions
nobody granted.

Their privacy state is decided in the same act rather than deferred: the person
and their directive are written together or neither is. After a successful add
the card re-reads the roster from Core rather than patching itself, because
someone added under a privacy directive may correctly not appear — showing them
anyway would display a person the household is not permitted to see.
