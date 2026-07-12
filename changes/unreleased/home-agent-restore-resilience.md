---
title: Harden Home Agent restore resilience
target: internal
type: fixed
---

Adds an isolated, networkless PostgreSQL restore drill with host-key-pinned
SFTP staging, bundled backups, page-checksum verification, and guarded cleanup.
Makes erasure-ledger replication idempotent across unchanged epochs while
preserving commit-marker ordering and encrypted off-host storage.
