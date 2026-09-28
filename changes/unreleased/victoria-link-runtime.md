---
title: Assemble the separate Victoria account-linking runtime
target: web
type: added
---

Connect Victoria's existing authenticated browser login, fresh-account proof,
session revocation, and private linking handoff components with durable storage.
Browser and coordinator listeners remain separate and require explicit TLS
provisioning; this change does not start them or grant shared-memory access.

Add an explicit executable entrypoint using bounded configuration and secret
files, private listener addresses, and cleanup if either listener fails to bind.
