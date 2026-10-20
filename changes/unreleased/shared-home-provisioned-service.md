---
title: Compose the provisioned Victoria adapter service
target: backend
type: added
---

Connect the Victoria runtime to its actual camera, MQTT, HTTPS, health and durable
storage components through a strict service launcher. Add authenticated local
timing-authority IPC and offline configuration validation. Explicit timing denial
or failed authority refresh invalidates cached health without waiting for the
next probe; pending evidence cannot extend freshness. Timing measurement
production, Linux acceptance, resource commissioning and deployment remain gated.
The installed MQTT add-on's documented ACL path failed isolated enforcement
checks; its supported customization path cannot apply the candidate repair.
Transport enforcement remains an explicit commissioning gate.
