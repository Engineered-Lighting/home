### Fixed

- Added an isolated, networkless PostgreSQL restore drill with host-key-pinned
  SFTP staging, bundled backups, page-checksum verification, and guarded
  cleanup.
- Made erasure-ledger replication idempotent across unchanged epochs while
  preserving commit-marker ordering and encrypted off-host storage.
