---
title: Govern Home Agent retention-worker health
target: backend
type: changed
---

Adds a fenced, database-timed worker-maintenance proof; suppresses sensitive
runtime-spool persistence when retention is unavailable; requires a fresh
worker proof for semantic rollout while preserving privacy-essential writes;
and advances Home Agent Core to migration `0006_worker_maintenance_health` with
restore verification for its unlogged health state.
