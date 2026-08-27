---
title: Deploy and verify the modules the Home Assistant freeze scripts import
target: backend
type: fixed
---

The two scripts the activation runs on the Home Assistant host import `identity_store` and `legacy_identity_fence`, but the installer copied neither and the runner's readiness check verified neither. An activation therefore passed its own prerequisites and then failed at the writer fence with an opaque transport error, after Home Assistant had already been stopped, because the remote `ImportError` is discarded. Both modules are now installed and digest-verified alongside the scripts that import them, and the readiness check refuses when either is missing.
