---
title: Pin the dormant Phase 3 activation source plan
target: backend
type: added
---

Add a read-only source-plan verifier that binds activation-relevant code to the
hosted-tested E5j commit and reports every still-missing executable boundary.
It cannot issue source acceptance, run migrations, change rollout mode, or
enable semantic writes.

The complete hosted E1-E5k PostgreSQL 17 authority gate passed in workflow run
`30394892306` at source commit `ba9edab82261000761c71760833ed97d81bb90f9`.
