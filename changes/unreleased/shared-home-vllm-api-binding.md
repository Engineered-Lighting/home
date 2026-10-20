---
title: Bind vLLM chat execution to authenticated admission requests
target: backend
type: added
---

Add signed exact-body dispatch bindings for snapshot and existing chat transports,
and pinned API hooks that register actual internal request IDs and await durable
cleanup before final JSON or streaming output. Preserve cleanup receipts after
client loss. Deployment and live acceptance remain outstanding.
