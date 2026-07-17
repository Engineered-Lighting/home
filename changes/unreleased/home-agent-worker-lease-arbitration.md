---
title: Arbitrate Home Agent worker leases without restart churn
target: backend
type: fixed
---

Adds the deployable `0006a_worker_lease_arbitration` hotfix so a second worker
cannot preempt a different instance whose PostgreSQL-timed heartbeat is no more
than 45 seconds old. A contending startup now remains idle and retries in its
existing process, while loss of an acquired fence still terminates fail closed.
Migration startup is pinned to this exact revision and verifies the resulting
Alembic state, so dormant descendant schemas cannot silently skip the hotfix.

This is split-brain and secondary-churn defense. The observed worker restart
loop was driven by repeated PostgreSQL recovery after a detached archive helper
was reaped by the database PID 1 topology, not by worker lease contention.
