---
title: Recover camera pilot without replaying uncertain requests
target: backend
type: fixed
---

Distinguish proven pre-dispatch health failures from uncertain model execution so a rejected health check does not permanently close the camera pilot. Add an offline, operator-only recovery path that requires stopped-engine evidence and preserves the original request state in an audit record. Victoria accepts authenticated terminal recovery status without replaying the request. Keep a bounded execution-completion wait separate from the unchanged observation deadline, and validate a supervised cold-start warmup before admitting live camera work.
