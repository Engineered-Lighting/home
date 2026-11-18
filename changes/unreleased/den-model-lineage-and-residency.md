---
title: Optional Den model accepts real transform lineage and keeps its warm worker
target: backend
type: fixed
---

The optional V-JEPA-LM worker and its control producer now accept Den transform IDs containing `@` (such as `den-oak@2026-11-13.1`), so a person in the Den no longer blocks every control receipt, and a failed source now names its fixed code (for example `dependencies_lineage_invalid`). Live clips never cold-start the worker: they are refused as `not_resident` until an operator warmup, and a clip that expires, is cancelled or loses its Den binding discards its result without unloading the resident model, while Ops backoff and other control failures, a job still running 20 seconds after dispatch, admission loss and resource faults still retire it, and deferred clips never extend the 120-second idle residency.
