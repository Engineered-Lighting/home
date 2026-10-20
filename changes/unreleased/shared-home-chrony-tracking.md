---
title: Prepare ongoing camera clock estimation
target: backend
type: added
---

Add a strict reader for provisioned chrony tracking reports, including source
binding, synchronization status and the estimated clock-error budget. Add a
bounded local-query sampler using the existing camera receipt format, with
expiry, clock discontinuity and reference-age checks. This is local preparation
only; no time service or camera deployment has changed. Stage a pinned Chrony
configuration and restricted service without uninstalling the existing clock
package, preserving the original service for rollback. Reuse the existing
protected receipt publisher with daemon/boot binding and unchanged short expiry;
retain its process lock until publication finishes.
