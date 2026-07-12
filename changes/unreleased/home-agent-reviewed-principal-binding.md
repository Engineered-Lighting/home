---
title: Add reviewed Home Agent identity binding
target: backend
type: added
---

Adds a two-party, expiring Home Assistant account-to-person binding ceremony:
the authenticated subject requests review and confirms an exact staged person,
while an isolated operator path can only review an opaque request code. Direct
binding authority is removed, location choices remain off, and the workflow is
protected by provenance constraints, privacy cancellation, retention, and
browser-origin security checks.
