---
title: Add scoped remote specialist HTTP transport
target: backend
type: added
---

Add an opt-in HTTPS client and ASGI worker ingress restricted to execution and
terminal lookup. Bind credentials to source site/cameras, bound request bodies,
reject redirects and duplicate JSON keys, and retain busy state while cancelled
requests still have local execution outstanding. No deployment is enabled.
