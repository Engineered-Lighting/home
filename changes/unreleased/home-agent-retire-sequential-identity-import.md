---
title: Retire sequential legacy identity imports
target: backend
type: changed
---

Retire the per-item legacy People migration capability and protocol endpoints.
Authenticated calls now return `capability_disabled` without parsing identity
content or opening a database transaction, and the obsolete Compose profile
exits before reading its credential or legacy database. Operational Frigate
recognition enrollment remains isolated and unchanged.
