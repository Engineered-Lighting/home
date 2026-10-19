---
title: Connect session erasure to runtime reconciliation and API
target: backend
type: added
---

Support explicit full local erasure through the authenticated deletion API.
Refuse active local users, clear stopped per-session runtime references and remove
registered staging leftovers. External inference copies still require deletion
propagation; the Home erasure control and native acceptance remain pending.
