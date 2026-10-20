---
title: Recover uncertain saved-analysis completion without replaying work
target: backend
type: added
---

Add a provisioned completion-lookup path for interrupted Perception Lab analysis.
Matching terminal proof restores each result once; unknown completion retains
the reservation. Recovery never starts inference or automatically resumes a job.
