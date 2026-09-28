---
title: Use existing API permissions for preference forgetting
target: backend
type: fixed
---

Create a running erasure request and record its completion after preference
scrubbing in the same transaction, using Core's existing column-scoped API
permissions. Completion still requires durable erasure-ledger acknowledgement.
