---
title: Add isolated identity authority executors
target: backend
type: added
---

Add separate, stdin-only operator entrypoints for the reviewed PostgreSQL
identity finalizer and semantic-authority cutover kernels. Both capabilities
remain inert behind expired, connection-limited roles; the executors validate
bounded private input, use complete `SERIALIZABLE` transactions, and emit only
content-free receipt IDs.
