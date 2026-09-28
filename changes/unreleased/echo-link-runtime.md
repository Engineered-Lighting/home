---
title: Compose the authenticated shared-account setup runtime
target: backend
type: added
---

Add an explicitly provisioned Echo runtime joining existing account-linking,
fresh authentication, review and durable revocation components to one session
store. The server accepts an explicit mounted configuration with separate secret
files and drains requests before closing journals. Production provisioning remains
separate; shared memory is not enabled by default.
