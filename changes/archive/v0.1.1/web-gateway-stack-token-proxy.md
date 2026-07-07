---
title: Proxy stack token through the Home web gateway
target: web
type: changed
---

The Tailscale web app can now use AI stack controls without storing
`STACK_TOKEN` in the browser; the Ubuntu gateway injects it server-side for
allowed supervisor API routes.
