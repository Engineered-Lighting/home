---
title: Pin the activation source to the People and relationships work
target: internal
type: changed
---

Bumps the accepted activation-source commit and its hosted PostgreSQL gate run
to cover the household read, the object-side erasure fix, the relationship
vocabulary and kernels, and the People tab flows.

Every one of those changes touched a pinned activation path — the core
application, the BFF source, the migrations, and the web panel — so each landed
as the fix half of the two-PR cycle. This is the pin half for all of them.
