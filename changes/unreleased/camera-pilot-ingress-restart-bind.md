---
title: Camera pilot restarts no longer fail on its own closing connections
target: backend
type: fixed
---

The camera pilot's private TLS ingress now sets `SO_REUSEADDR` on Linux, so a restart binds its port even while the old instance's connections are still closing (TIME_WAIT or an orphaned FIN-WAIT-2). Before, a restart could fail with "Address already in use" for about a minute. Two listeners still can't share the port, and the flag stays off on other platforms. A failed bind now reports the bind error itself; before, the live-scene ingress hid it behind an `AttributeError` from its cleanup.
