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
Hosted PostgreSQL 17 acceptance passed in workflow run `30329873575`.
Production remains pinned to revision 0006a in record-only mode pending an
explicit operator rollout.
