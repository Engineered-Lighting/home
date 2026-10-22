---
title: Keep Home running when a WebSocket connection resets
target: web
type: fixed
---

The Home gateway now handles errors on upgraded client sockets and retires both ends of failed proxy connections. A disconnected or reset browser connection no longer terminates the gateway and interrupts live inference displays. Authentication and HA token rewriting remain unchanged.
