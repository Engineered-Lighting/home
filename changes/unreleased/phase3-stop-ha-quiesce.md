---
title: Wait for the legacy database to go quiet before freezing the writer
target: backend
type: fixed
---

`ha core stop` returns when the supervisor accepts the request, not when Home Assistant has finished flushing, so the legacy identity database could still carry a `-wal` sidecar when the activation moved on. The step 20 writer fence refuses exactly that, and reported it as an opaque transport failure with the remote error discarded. Stopping Home Assistant now polls the stopped-database probe until the file is provably static, on the success path as well as after a lost SSH response, and fails closed if it never settles.
