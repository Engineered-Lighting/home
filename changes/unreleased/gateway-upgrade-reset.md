---
title: Keep Home available after a WebSocket disconnect
target: web
type: fixed
---

Home's gateway now handles errors on upgraded client sockets and closes their abandoned upstream tunnels. A client connection reset no longer terminates the gateway and interrupts other Home connections.
