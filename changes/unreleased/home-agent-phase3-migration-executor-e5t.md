---
title: Add a bounded Phase 3 migration executor
target: backend
type: added
---

Phase 3 now has a root-only executor for its five exact migration stops. It
requires fresh separately armed evidence, the hosted-tested source pack, all
application-facing services stopped, and exact before/after revision guards;
it cannot choose an arbitrary revision, build or pull images, or start the
deployment.
