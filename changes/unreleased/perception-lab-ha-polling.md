---
title: Connect recording-owned HA state polling
target: backend
type: added
---

Add bounded HTTPS polling for provisioned HA entities, tied to Lab recording
startup, stop and shutdown. Retain scalar state and acquisition uncertainty only;
late reads are discarded after stop. Actual endpoint and credential commissioning
remain required before enablement.
