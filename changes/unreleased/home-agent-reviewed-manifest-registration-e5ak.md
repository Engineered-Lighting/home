---
title: Register the reviewed identity manifest so the finalizer has provenance
target: backend
type: added
---

`commit_finalizer` copies its provenance out of
`operations.reviewed_identity_migration_runs`, and nothing ever wrote a row
there. The manifest is produced by the signing ceremony, but that ceremony runs
every phase with `PrivateNetwork=yes` and `IPAddressDeny=any` around the Ed25519
keys and the raw People content, so it cannot reach PostgreSQL — and must not be
able to. Registration therefore uses the same stdin bridge the admission writers
use: the operator pipes one private manifest into a one-shot container, and a
Core module makes exactly one kernel call.

- `app/identity_migration_registrar.py` reads the manifest on stdin, refuses
  anything the kernel would reject, and calls
  `operations.register_reviewed_identity_migration` once under `SERIALIZABLE`,
  retrying only the whole transaction. It keeps its own database URL check
  pinned to `home_agent_identity_migration`, because the admission writer's is
  pinned to `home_agent_owner` and the kernel refuses every caller but the
  migration login.
- A new `identity-registrar` compose service mounts the migration login's URL,
  which the deployment declared but nothing consumed. The retired sequential
  `identity-migration` service is untouched and stays `network_mode: none`.
- The activation runner rebuilds the manifest from the private signing state —
  the same `{run, source_items, decisions}` split the sealed compiler produces,
  with the review signature folded into the run — and registers immediately
  before writing the admission that consumes it.

Registration is folded into the existing `commit_finalizer` handler rather than
added as a step. The journal requires `next_step` to equal
`STEPS[len(completed_steps)]`, so inserting a step would make the live 16-step
journal unloadable with no repair path. Keeping the two together also matters
because registration is one-shot for the life of the database: there is exactly
one `record_only -> shadow` authorization, exactly one run per authorization,
and no role holds `DELETE` on the runs table. Re-running is safe — an identical
manifest replays and the kernel returns the same run identifier.

This also fixes a defect in the authority ceremony. It activated its login with
`_role_ceremony("activate", command.name)`, but the command names are `finalize`
and `cutover` while the activation script's targets are `finalizer`, `cutover`,
and now `migration`. `cutover` worked only because the two words coincide;
`finalize` was refused as an invalid target, so the finalizer execution at the
end of step 17 could never have activated its login. Commands now carry an
explicit role, and a test cross-checks every role against the script's own
vocabulary. Nothing caught it because the suite stubs the role ceremony with a
fake that accepts any string, and the hosted gate exercises the shell script
directly rather than through the ceremony module.
