---
title: Recover proven inference rejections and validate source health
target: backend
type: added
---

Add durable, request-bound gateway rejection receipts so Victoria can recover
without replaying uncertain submissions. Resource manifest schema 2 requires an
explicit ledger-record limit shared by accepted and rejected requests, with the
storage floor enforced on both insertion paths.

Add bounded camera and Frigate health probes that preserve source measurement age
and require separately validated clock and exact-snapshot timing evidence. Live
provisioning and deployment remain gated.
