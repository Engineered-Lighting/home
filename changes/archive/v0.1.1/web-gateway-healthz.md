---
title: Add /healthz liveness endpoint to the home web gateway
target: web
type: added
---

The home web gateway now answers `GET` and `HEAD /healthz` before auth with a
`no-store` JSON body reporting `status`, the running asset `commit`, and process
`uptime`, so an external watchdog can poll liveness on a locked-down gateway
without tripping the login flow.
