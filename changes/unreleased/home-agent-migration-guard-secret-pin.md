---
title: Pin the corrected migration revision guard as the activation source
target: backend
type: changed
---

Phase 3 activation now admits the hosted-tested migration revision guard
correction as its exact source pack, so the reviewed migrations can verify
the database revision with the database secret loaded.
