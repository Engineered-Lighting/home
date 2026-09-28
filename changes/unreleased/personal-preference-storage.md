---
title: Add governed preference storage lifecycle
target: backend
type: added
---

Implement transaction-scoped preference review, confirmation, correction and
forgetting using Core's existing memory tables and deletion ledger. Corrections
bind both fact identity and revision. Revisions survive forgetting, and a later
explicit preference receives a new fact identity.
Production roles, authenticated routes and Home integration are not enabled by
this change.
