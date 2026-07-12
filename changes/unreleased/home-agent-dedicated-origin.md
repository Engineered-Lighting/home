---
title: Isolate the Home Agent web and OAuth origin
target: stack
type: added
---

Adds a loopback-only, tailnet-fronted Home Agent origin as a separate service
and deployment lifecycle. It serves only the built private Agent surface and
explicit browser BFF routes, strips ambient authority headers and unrelated
cookies, rejects legacy/native proxy paths, and never logs OAuth callback query
strings.
