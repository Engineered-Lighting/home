---
title: Add a trusted shared-preference review from Home chat
target: web
type: added
---

Recognize evening-lighting preference requests and open a dedicated Agent-origin
review without exposing its cookies or confirmation authority to the main chat.
The review supports explicit confirmation and outcome lookup; reading a private
preference back into Home requires an explicit Show in Home gesture. Production
enablement requires provisioned parent origins and Core permissions.
Clearing the conversation, switching homes, or changing the connection closes
pending preference review immediately without resubmitting any write.
