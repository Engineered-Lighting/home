---
title: Add a durable local Home context shadow journal
target: backend
type: added
---

Add a loopback-only, explicitly granted Home context journal with atomic snapshots, durable retries, evidence request lifecycle records, and worker checkpoints committed with shadow output. Each room has one configured worker authority, and shared engagement and lighting output require atomic worker commits. Durable intent application results remain available to reconnecting clients through scoped intent lookup. The service preserves scoped provenance and rejects revoked or unverified inputs; it does not contact models, acquire camera data, or actuate lighting.
