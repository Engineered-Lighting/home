---
title: Add the authenticated two-parent staging kernel
area: home-agent
---

- Added revision `0019_parent_stage_e5e`, which stages one
  digest-bound, 15-minute private preview containing exactly two reviewed
  parent candidates.
- The child is derived from the authenticated Home Assistant binding and both
  parent identities are derived from the current reviewed People migration
  lineage; callers cannot supply any person or legacy-label identifier.
- Added a dedicated table-invisible `NOLOGIN` kernel role and a
  first-statement-only `SERIALIZABLE` `SECURITY DEFINER` function.
- Re-runs identity-authority, privacy, retrieval-block, ambiguity, and
  existing-parent-fact checks under the global semantic write fence.
- Grant replay revokes the staging function before all admission checks and
  restores only exact `EXECUTE` to the table-blind binding committer at the
  exact E5e revision.
- No parent fact, confirmation artifact, memory transaction, authority
  receipt, BFF route, or UI activation is included. Production remains pinned
  to revision `0006a_worker_lease_arbitration` in `record_only`.
