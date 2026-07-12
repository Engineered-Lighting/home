---
title: Contain direct Home Agent parent confirmation
target: backend
type: changed
---

Removes direct parent-fact confirmation from the Core service API and every
deployed BFF allowlist, so caller-supplied parent/child UUIDs cannot create a
single relationship edge. Explicit parent facts remain disabled until a
reviewed server-staged candidate, digest-bound private preview, and atomic
authenticated two-parent confirmation flow is implemented and adversarially
verified.
