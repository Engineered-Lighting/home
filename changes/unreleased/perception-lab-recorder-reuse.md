---
title: Allow repeated Lab recording sessions after confirmed shutdown
target: backend
type: fixed
---

Reset session-specific recorder state after its previous process and monitor have ended, allowing another trial without restarting the controller. Failed termination remains indeterminate and cannot be mistaken for a stopped recording or replaced by another capture process.
