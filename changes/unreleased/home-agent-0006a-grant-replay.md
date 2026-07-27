---
title: Keep Home Agent grant replay compatible with the live revision
target: backend
type: fixed
---

Allow the fail-closed runtime grant replay to run while production remains
pinned to revision `0006a_worker_lease_arbitration`, where dormant Phase 3
identity tables do not exist. Any partial later table set is quarantined and
still rejected.
