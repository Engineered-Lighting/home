---
title: Fail-closed Linux database gate
target: internal
type: changed
---

Restricted high-churn Home Agent PostgreSQL gates to an explicitly admitted
GitHub-hosted workflow or approved disposable host. The AI host is blocked by
both process-host and Docker-daemon identity before database test or scheduled
restore workloads can start; restore catch-up after reboot is disabled and the
remaining restore process has explicit resource limits.
