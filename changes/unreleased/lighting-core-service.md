---
title: Core lighting consent, review and dispatch service
target: internal
type: added
---

Core gains the inactive private service for explicit cross-home lighting: a
dedicated database role that can only write lighting grants, a one-time owner
consent for both homes, signed and expiring action reviews, an encrypted
journal that records each dispatch before any home is asked to act, and
outcome lookups that never resend. Nothing is deployed or enabled yet.
