---
title: Process current desk-preview results before the next camera capture
target: backend
type: fixed
---

The optional desk-preview path now wakes its existing camera pump when analysis completes, so a current result can be processed while the pump waits for its next capture. The capture schedule, original expiry and Stop behavior are preserved; this fix is validated offline and has not been deployed.
