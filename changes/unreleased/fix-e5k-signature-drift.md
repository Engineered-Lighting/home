---
title: Fix the signature drift that made the owner-partner migration fail
target: internal
type: fixed
---

Migration `0024` declared a fifteen-argument kernel and then named a
fourteen-argument one in its `ALTER FUNCTION` and `DROP FUNCTION`. The
attestation-artifact parameter had been added to the declaration and to the
call, but the signature was restated by hand in three places and only two were
updated.

PostgreSQL resolves functions by signature, so nothing caught it statically. The
migration created the function, then failed on the next statement with
`function ... does not exist` — after five earlier migrations had already
applied in the same run. Alembic rolled the whole run back, so no partial state
resulted, but the deployment could not proceed.

The signature is now declared once per migration and interpolated, in `0024`,
`0026` and `0027`. A test requires that convention for every migration from
`0022` onward that creates a function and refers to it again.
