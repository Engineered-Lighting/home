---
title: Give the identity migration login a bounded activation path
target: backend
type: added
---

Registering a reviewed identity manifest calls
`operations.register_reviewed_identity_migration`, whose `EXECUTE` privilege is
held only by `home_agent_identity_migration`. That login is provisioned `VALID
UNTIL '1970-01-01'` and had no activation path at all —
`IDENTITY-MIGRATION-ROLE.md` said a later project "must add a separate
root-only, time-bounded activation and password rotation mechanism; this
milestone intentionally provides none". Without it the manifest can never be
registered, and without a registered manifest `commit_finalizer` has no
provenance to copy and the activation cannot pass step 17.

`activate-identity-authority-role.sh` now accepts `migration` as a third
target, alongside `finalizer` and `cutover`. It is the same ceremony, not a
second program: it still requires root, the armed E5m grant permit, the
database at `0015_current_authority_e5a`, the role's provisioned shape
(`NOINHERIT`, no superuser or bypass-RLS, connection limit one), and no session
already open as that role. It grants a two-minute `VALID UNTIL` window, proves
the window afterwards, and `deactivate` re-expires the login and terminates any
session it opened.

Password rotation remains deliberately absent, and the ceremony's scope makes
that safe to state: it alters `VALID UNTIL` and nothing else, so the persisted
database URL keeps whatever secret provisioning set. A contract test asserts the
ceremony never issues `ALTER ROLE … PASSWORD`.
