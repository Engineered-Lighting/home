---
title: Bound private shared-preference database connections
target: backend
type: fixed
---

Limit each private preference and account-linking listener's Core pool to two
connections without overflow. Existing Core service defaults remain unchanged;
deployment still requires an aggregate database connection budget.
