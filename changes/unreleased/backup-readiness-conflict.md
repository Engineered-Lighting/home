---
title: Report readiness conflicts without blocking backup inspection
target: backend
type: fixed
---

Report HTTP 409 from legacy readiness diagnostics as a failed readiness input
instead of aborting the entire read-only report. Migration admission stays
blocked, while the existing off-host backup writer can independently verify a
healthy encrypted repository. Authentication and transport errors still fail.
