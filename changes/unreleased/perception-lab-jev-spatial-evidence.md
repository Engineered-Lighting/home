---
title: Preserve object geometry in Lab reasoning evidence
target: backend
type: fixed
---

Preserve bounded object boxes and frame-local person-to-skeleton associations in
JEV evidence. Use verified recorded frame dimensions when detector results omit
them; reject invalid geometry and ambiguous associations without forwarding
worker identities or private attributes. Existing recordings remain readable.
