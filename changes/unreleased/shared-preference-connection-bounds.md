---
title: Bound private shared-preference database connections
target: backend
type: fixed
---

Limit each private preference and account-linking database pool to one connection
without overflow. Allow existing Core main and operator pools to share explicit
deployment limits while preserving their defaults. Deployment still requires
verification of the aggregate database connection budget.
