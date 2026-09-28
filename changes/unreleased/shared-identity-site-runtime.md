---
title: Compose private per-home authentication and logout runtime
target: backend
type: added
---

Combine existing challenge-bound authentication and session-revocation handlers
in a separately provisioned per-home Core application. Keep dedicated database
roles and credentials, check admission before dispatch, and preserve logout when
optional authentication work is suspended. Add an explicit private TLS entrypoint
with file-based credentials and ordered Core startup/shutdown. No production
listener is enabled without separate provisioning.
