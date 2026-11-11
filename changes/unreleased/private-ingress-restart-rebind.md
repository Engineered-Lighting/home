---
title: Private TLS ingress restarts no longer fail while old connections linger
target: backend
type: fixed
---

The private mutual-TLS ingress (the camera pilot's :9446 and the other services built on it) now sets `SO_REUSEADDR` on Linux. A restart now binds at once instead of failing with `EADDRINUSE` for up to about 60 s while the previous instance's closed connections linger in FIN-WAIT-2 or TIME-WAIT. A second listener on a live port is still refused. The first restart onto this release can still wait once, because the kernel also needs the option on the old instance's sockets.
