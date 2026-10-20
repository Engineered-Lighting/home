---
title: Count the camera's complete timing uncertainty once
target: backend
type: fixed
---

Bundled camera input now uses the validated schema-3 header's complete timing uncertainty without adding the same host-clock error a second time. The full uncertainty, exact clock identity, original expiry and existing uncertainty limits remain required; this correction has not been deployed.
