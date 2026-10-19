---
title: Add bounded authenticated detector worker lifecycle
target: backend
type: added
---

Add a durable, single-worker lifecycle supervisor with asynchronous start/status,
epoch-bound stop, idempotent request IDs and fixed expiry. Reuse the reviewed LA
health/resource launcher and expose only fixed detector proxy routes over TLS.
Keep unresolved launches reserved across restart and distinguish proven preflight
refusal from uncertain launch. The supervisor remains undeployed pending paired
controller integration and validation.
