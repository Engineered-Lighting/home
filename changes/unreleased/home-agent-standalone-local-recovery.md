---
title: Support standalone Home Agent recovery
target: internal
type: changed
---

Documents the encrypted same-host pgBackRest recovery topology for standalone
Ubuntu deployments without a FormD or NAS dependency, while retaining explicit
off-host durability, erasure-ledger replay, isolated-restore, and E1/E2 host
quarantine gates. Adds a fresh archive-spool epoch, coordinated repository
locking, fail-closed local configuration validation, capacity bounds, and a
root-installed, digest-checked, resource-limited daily full backup schedule.
