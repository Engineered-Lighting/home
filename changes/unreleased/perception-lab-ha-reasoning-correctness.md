---
title: Keep Lab HA context from driving or refreshing JEV reasoning
target: backend
type: fixed
---

Lab JEV freshness and dispatch now depend only on camera observations: HA-only packets are never sent, a fresh HA poll cannot make stale visual evidence current, and latest-only arms keep their latest visual observation plus at most the newest HA snapshot already recorded when that frame was captured. Camera-derived HA occupancy carries an explicit same-camera correlation marker (projection `structured-semantic-3`, semantics `den-semantics-2`), request-validation errors are recorded as failed instead of unknown delivery, and recipes whose history exceeds 10 seconds are rejected at startup. The HA source file is checked against the JEV bindings at controller startup and may name a private CA file and a pinned hostname; if the file becomes invalid later, that recording continues with HA marked unavailable.
