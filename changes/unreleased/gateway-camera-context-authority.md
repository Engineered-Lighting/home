---
title: Let signed-in Home Chat verify its camera context
target: web
type: fixed
---

Provide the opaque authenticated identity identifier that Home Chat already requires before handling messages and private camera context. Signed-out sessions receive no identifier and remain refused. The identifier is derived from the existing authentication state without exposing the session cookie or adding stored application data.
