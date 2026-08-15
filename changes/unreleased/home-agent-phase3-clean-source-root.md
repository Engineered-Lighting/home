---
title: Bind Phase 3 signing to the clean activation checkout
target: backend
type: fixed
---

The TPM-backed Phase 3 signing provisioner now verifies its installed tools,
policy, schema, and hosted-accepted source plan against the detached clean
activation checkout instead of the mutable live development checkout.
