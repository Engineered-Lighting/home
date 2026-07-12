### Home Agent Phase 3 finalizer foundation

- Added revision `0009_identity_finalizer_base` with normalized,
  content-minimized lineage from one reviewed apply receipt to one typed
  semantic target and its affected person IDs.
- Added a separate expired-by-default `home_agent_identity_finalizer` login and
  `NOLOGIN` owner kernel. They receive no table, function, schema, sequence,
  type, default, bearer, API, or activation authority.
- Added independent root-owned role secrets, strict materialization/preflight,
  and a PostgreSQL-only operator service that exits before opening its database
  URL because no finalization function exists yet.
- Hardened grant replay to quarantine both finalizer roles, including future
  default privileges, while leaving revision 0008 manifest registration
  unchanged.
- Kept semantic finalization, authority cutover, parent confirmation, memory,
  greetings, model retrieval, and physical action disabled. Production remains
  pinned to revision `0006_worker_maintenance_health` in `record_only` mode.
