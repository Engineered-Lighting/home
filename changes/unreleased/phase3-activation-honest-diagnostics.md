---
title: Report why a Phase 3 activation step failed instead of one generic error
target: backend
type: fixed
---

The activation runner, the migration executor, and the root admission bridge all
ran their subprocesses with `stderr=subprocess.DEVNULL` and raised one identical
message for three unrelated conditions — a non-zero exit, oversized output, and a
NUL byte in output. A failing step reported only "failed closed", which cost a
full diagnostic session to root-cause the frozen-DDL defect in revision `0007`.

Each refusal now carries a categorical code from a closed vocabulary
(`exit_nonzero`, `stdout_empty`, `stdout_oversize`, `stdout_nul`, `timeout`,
`spawn_failed`), and that code is what the journal records.

The journal's privacy contract is unchanged: it still holds only random
identifiers, the accepted source commit, ordered step codes, attempt counts, and
categorical codes. Codes are validated against the closed vocabulary when the
error is constructed, so free text cannot reach the journal even if a caller
passes it.

A redacted stderr tail is printed to the operator's terminal only, and only for
subprocesses that cannot carry household People data — the compose lifecycle
calls, `docker inspect`, the grant application, and the migration executor's own
compose calls. The restore drill, both backup writers, the signing phases, the
privacy observer, the legacy snapshot, the credential provisioner, and the
identity admission containers are excluded by an explicit opt-in at each call
site plus a deny check that a mistaken opt-in cannot bypass. Credential userinfo
is stripped from any tail that is printed.

The admission bridge never echoes a tail at all. It names only governed kernel
error identifiers such as `identity_finalizer_live_run_mismatch`, which are a
closed snake_case vocabulary carrying no household content.
