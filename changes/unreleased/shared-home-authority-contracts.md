---
title: Add staged shared-home authority and memory contracts
target: backend
type: added
---

Add workstation contracts for issuer-qualified owner links, revocable permissions, frozen lighting intents, governed memory candidates and a separate Victoria observation store with independent pruning, deletion propagation and consistent SQLite backups. Production authority remains disabled, and restored records require authoritative deletion replay before access.
