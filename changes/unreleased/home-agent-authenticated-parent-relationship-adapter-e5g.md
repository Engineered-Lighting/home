---
title: Add authenticated parent relationship adapter
target: backend
type: added
---

Adds the dormant E5g split-credential Core and browser BFF adapter for staging
and atomically confirming the reviewed two-parent relationship. The browser
supplies only opaque ceremony protocol values; Home Assistant identity and both
semantic relationship edges are rederived by the authenticated PostgreSQL
kernels. The routes activate only at exact revision `0020_parent_commit_e5f`;
the production `0006a_worker_lease_arbitration` record-only deployment, native
client, Agent UI, location memory, and travel greetings remain unchanged.
