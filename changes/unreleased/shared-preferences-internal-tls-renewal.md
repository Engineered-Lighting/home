---
title: Renew shared-preference internal TLS leaves and warn before they expire
target: backend
type: added
---

Adds a reviewed command that reissues each private shared-preference listener's TLS leaf from the retained internal CA with its existing subject, IP SAN, key policy, owner and mode, keeps the displaced leaf for rollback, and refuses to run when the CA is near expiry or client trust has changed. A daily host timer pages through the existing ntfy alert path 30 days before any leaf expires and 120 days before the CA does, and the shared-preferences README gains the renewal order, session impact and rollback steps.
