---
title: Compose durable account-linking coordinator runtime
target: backend
type: added
---

Connect existing account-link issuance, proof verification, owner review, and
confirmation handlers with encrypted recovery journals and bounded private
request admission. Separate credentials and database roles remain required;
this change does not enable a production listener or grant memory access.
Add an explicit private TLS entrypoint with file-based credentials and Core
admission, retaining the coordinator's authentication and concurrency middleware.
Include consent recovery and private identity/coordinator runtime checks in the
pinned preference-authority validation gate before deployment.
