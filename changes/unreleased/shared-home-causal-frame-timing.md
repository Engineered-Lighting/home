---
title: Add exact-frame camera timing validation
target: backend
type: added
---

Add a distinct camera metadata contract binding causal timing evidence to an exact frame and JPEG. Validate challenge order, exposure and clock uncertainty without extending the receipt to later frames, and reject oversized numeric metadata safely in the adapter and generated HA consumer. Live enablement remains subject to camera and host-clock commissioning.
