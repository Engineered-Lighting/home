---
title: Fix Phase 3 credential provisioning
target: backend
type: fixed
---

Phase 3 signing credentials now bind to the reviewed policy file's raw deployment digest and derive its version from that same document, matching the production Compose and environment contracts. The activation runbook also documents the persistent HAOS Python prerequisite.
