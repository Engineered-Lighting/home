---
title: Preserve unknown model observation times
target: backend
type: fixed
---

Home can display the configured movement-intent model while its worker has no observed status. Unknown observations now omit their expiry; genuine status leases retain their original times when republished or expired.
