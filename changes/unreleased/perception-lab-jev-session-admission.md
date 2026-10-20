---
title: Enable JEV for explicitly started bounded Lab sessions
target: backend
type: changed
---

Replace the expiring commissioning grant with explicit per-session admission.
JEV remains off by default and is limited to five minutes from recording start,
120 requests per minute and 600 requests per session across both arms. Toggling
reasoning does not reset the deadline or durable call budget. Stopping, disabling
reasoning or revoking the operator policy prevents further admission; replay
never starts inference.
