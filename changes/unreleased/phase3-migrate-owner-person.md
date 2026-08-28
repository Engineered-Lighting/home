---
title: Add the migrate command that reaches the owner-person revision
target: internal
type: fixed
---

Migrations `0022`–`0027` shipped with no entrypoint command able to reach them.
The `migrate` role refuses any target but the baseline, and each Phase 3
revision is reached by its own command with a fixed target — so the six new
migrations were undeployable, and the attempt failed with
`HOME_AGENT_EXPECTED_DB_REVISION is not deployable by this image`, which reads
as a configuration error rather than a missing command.

Adds `phase3-migrate-owner-person`, and tests asserting that the head of the
migration chain and the newest readiness member each have a command that
reaches them, that no command targets a revision without a migration, and that
each command still refuses arguments.

The six migrations also now declare `down_revision: str | None`, matching every
other migration; the previous form is why a chain walk saw twenty-two heads
instead of one.
