---
title: Add governed preference storage lifecycle
target: backend
type: added
---

Implement transaction-scoped preference review, confirmation, correction and
forgetting using Core's existing memory tables and deletion ledger. Revisions
survive forgetting so a later explicit preference does not reset history.
Production roles, authenticated routes and Home integration are not enabled by
this change.
