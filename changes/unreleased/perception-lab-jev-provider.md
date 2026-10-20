---
title: Add explicitly authorized Perception Lab JEV provider adapter
target: backend
type: added
---

Add bounded JEV requests with independent arm provenance, exact response-version
checks, and authorization checks before sending and publishing. Household cloud
reasoning remains disabled until its trusted egress verifier and scheduler are
connected and validated; public-evaluation approval does not enable it.

Persist independent per-arm reasoning dispatch and replayable judgments, with
bounded pending work, cadence limits, stale-generation rejection and deletion
guards for uncertain execution. Keep specialist observations separate from JEV
replay columns and score experiment generations separately.
