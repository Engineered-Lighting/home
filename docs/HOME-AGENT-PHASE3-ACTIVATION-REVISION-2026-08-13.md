# Home Agent Phase 3 activation revision — 2026-08-13

## Decision

Keep the greenfield Home Agent architecture and its People, Place, privacy,
and action-safety boundaries. Revise the live Phase 3 sequence before semantic
cutover. The record-only deployment has met its evidence gate, but installed
database kernels and bounded executors are not equivalent to a complete,
reviewable activation ceremony.

Production remains at `0006a_worker_lease_arbitration` in `record_only` until
every boundary below is implemented, hosted-tested, source-pinned, and then
reviewed against a fresh backup packet.

## Implementation status update

All five source boundaries identified by the rehearsal are now implemented in
the worktree, including the HA Edge privacy-policy receipt, live privacy
observer, isolated binding/parent kernel provisioners, and the authoritative
split-phase runner. The runner has a root-owned fsynced journal, exact ordered
steps, idempotent artifact handling, three explicit private pauses, post-start
container health checks, and forward-only containment after the legacy writer
freeze.

This closes the source-code gap; it does not authorize the live migration.
The updated source pack must still pass the pinned GitHub-hosted PostgreSQL and
web gates, be committed and pinned into the source verifier, and be installed
unchanged on Ubuntu. Production remains at revision 0006a until that hosted
acceptance and the private People review are complete.

## Evidence already complete

- Home Assistant OAuth/BFF identity works on the private Agent surface.
- HA Edge capture recovered after the Python 3.14 blocking-I/O regression.
- The Phase 2 gate is complete with 522 qualifying redacted envelopes against
  the required 500.
- A fresh encrypted full backup, checksum-verified OneDrive copy, isolated
  restore drill, and independent erasure-ledger check pass for the same backup.
- The finalizer, semantic-cutover, authenticated-binding, parent-authority, and
  parent-status database kernels have hosted PostgreSQL coverage.
- Fixed migration checkpoints, disposable authority roles, one-time admission
  writers, bounded role activation, and private web confirmation routes exist.
- Location memory, travel greetings, and model-originated physical actions
  remain disabled.

## Rehearsal finding (closed in source, pending hosted acceptance)

The prior source plan reported the activation contract as installed after the
low-level executors landed. A dry rehearsal showed five higher-level production
boundaries were still absent:

1. A private reviewed-identity packet compiler and distinct-purpose signing
   ceremony for the exact current People snapshot.
2. A writer-freeze evidence writer that binds the physically fenced HA legacy
   database, its external witness, the finalized migration run, and the exact
   source installation without copying private rows into logs.
3. A deterministic privacy-cutover evidence writer covering ingress,
   retrieval, prompt, initiative, export, and edge-block checks after the
   writer freeze.
4. A cutover packet compiler and distinct cutover signature over the exact
   finalization, freeze, privacy, erasure-ledger, policy, and release evidence.
5. One authoritative, resumable split-phase runner with an fsynced journal,
   exact revision guards, stop/start boundaries, bounded permits, and explicit
   forward-recovery/degraded rollback behavior.

Running the available commands manually would risk stopping at revision 0015
with a finalized People graph but no admissible cutover, or freezing the HA
legacy authority without a verified path to promote Core. That half-cutover is
now an explicit source-admission blocker.

## Revised implementation milestones

### E5w — activation completeness gate

- Publish the five missing boundaries in the content-minimized source plan.
- Refuse a new source-acceptance receipt while any boundary remains.
- Keep any previously written receipt insufficient after source drift; the
  final preflight must bind the subsequently hosted-tested source pack.

### E5x — reviewed People packet

- Capture one consistent legacy `identity.db` directly from stopped Home
  Assistant to `/srv/home-agent/private/phase3-identity/identity.db`. The
  root-only ceremony restarts HA from a failure-safe boundary, refuses active
  WAL/journal state, verifies the remote/local digest and SQLite integrity, and
  never stages private bytes in OneDrive or an off-host backup.
- Read the stopped legacy Identity Store through the existing allowlisted,
  query-only reader.
- Preserve stable person UUIDs, reviewed names and aliases, executable privacy
  directives, reviewed Frigate bindings, archived status, and legacy labels.
- Suppress ignored/do-not-identify content and record explicit omissions.
- Compile canonical source, decision, projection, and lineage manifests.
- Require a private content review before two distinct Ed25519 purposes sign
  the review and finalization payloads.
- Import `parent` only as `legacy_role_label(..., perspective=unknown)`; never
  create `parent_of` during migration.

### E5y — freeze, privacy, and cutover evidence

- Stop Home Assistant for the physical legacy semantic-writer fence.
- Preserve and independently bind the generation-2 external witness.
- Admit content-minimized writer-freeze evidence only after blocked-write,
  schema, WAL, sidecar, and source-installation checks pass.
- Re-run all six privacy surfaces against the finalized subjects and current
  erasure ledger.
- Compile and separately sign the exact semantic-cutover document.

### E5z — authoritative split-phase activation

- Phase A: re-run preflight, take/bind a fresh backup, stop Agent surfaces,
  migrate to the finalizer and current-authority checkpoints, and finalize the
  reviewed People packet.
- Phase B: fence the legacy HA writer, record freeze/privacy evidence, admit and
  commit semantic cutover, then migrate to the authenticated-binding revision.
- Pause with Core private and location capabilities contained while Marcelo
  confirms the authenticated HA-account binding.
- Phase C: migrate through parent authority/status, stage exactly two reviewed
  legacy parent candidates, and let Marcelo atomically confirm both facts.
- Every phase is idempotently resumable by an fsynced content-free journal.
  After irreversible boundaries, rollback means forward recovery or
  read-only/degraded containment, never restoring the old semantic authority.

## User-visible acceptance

After E5z, the private Agent surface will first ask Marcelo to confirm that the
authenticated Home Assistant account maps to the reviewed Marcelo People row.
It will then show Amelia and Marcelo Sr. as two separately sourced candidates
and ask for one atomic confirmation of both `parent_of` facts. No visit or face
observation is required to test that slice. Frigate may later recognize a
visiting parent through the migrated binding, but recognition remains sensor
evidence and does not create or authorize the family relationship.

Itaipava place memory begins only after this identity slice passes end to end.
