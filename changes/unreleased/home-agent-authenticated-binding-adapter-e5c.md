---
title: Add the split-credential authenticated binding adapter
target: backend
type: added
---

Add a separately gated E5c adapter that rechecks Home Assistant identity for
each principal-binding step, resolves reviewed proposals with the staging
credential, and commits through a table-blind PostgreSQL credential whose
first statement is the governed E5b kernel. Stable UUIDv7 output identities
make retries idempotent, while location memory and greetings remain off.
Production remains pinned to revision 0006a in record-only mode pending hosted
acceptance and explicit rollout.
