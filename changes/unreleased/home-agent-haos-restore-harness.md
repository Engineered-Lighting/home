---
title: Add an isolated Home Assistant OS restore harness
target: stack
type: added
---

Adds a source-only, fail-closed HAOS 18.1 recovery harness with ephemeral LUKS
storage, capability-free QEMU/network isolation, two-phase reviewed egress,
localhost-only access, redacted validation, authenticated durable cleanup
receipts, and guarded cryptographic erasure.
