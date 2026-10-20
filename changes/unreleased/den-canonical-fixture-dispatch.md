---
title: Prepare shared canonical ambient and task command ownership
target: backend
type: added
---

Add a bounded same-journal command owner for automatic ambient and spotlight
commands, using one durable ID sequence and the pinned adapter's original
PREPARE/COMMIT gate. Exact admitted cuts and actual consumer receipts are retained
before simulated effects; uncertain outcomes block further dispatch without
retries or counter resynchronization. The old outbox and issuer remain unchanged.
One supervised adapter call owns the shared namespace through bounded observation
and outcome retention. A timeout fences late gates without claiming cancellation;
drain reports live handles and requires acknowledged durable quiescence before
reporting a healthy retirement. Original monotonic timing and actual post-return
receipt clocks remain separate from grant creation and observation clocks.
This source prerequisite does not connect Home to physical transports, qualify
LED spectra, or complete the separate canonical archive and room-commit join.
