---
title: Preserve forgotten preference deletion during restore
target: backend
type: added
---

Add an explicit personal-preference deletion scope to Core's existing erasure
ledger and restore path. It scrubs preference history and pending reviews and
blocks derived artifacts. This prepares shared-memory forgetting; preference
writing and production enablement remain separate.
