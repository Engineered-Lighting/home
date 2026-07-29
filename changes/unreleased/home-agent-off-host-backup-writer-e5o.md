---
title: Add verified OneDrive backup export
target: backend
type: added
---

Add a root-only writer that snapshots only the encrypted pgBackRest repository,
uploads it to a fixed operator-controlled OneDrive remote, downloads the remote
bytes for an exact checksum comparison, and writes the Phase 3 off-host receipt
only after verification succeeds. The writer excludes PostgreSQL runtime data,
raw GPS, the erasure ledger, backup passphrases, and Home Assistant credentials.
