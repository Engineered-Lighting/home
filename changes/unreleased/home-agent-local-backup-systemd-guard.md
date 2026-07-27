---
title: Repair Home Agent backup unit guards
target: internal
type: fixed
---

Replaces unsupported `ConditionPathIsRegular` unit directives with failing
`ExecStartPre` regular-file checks so a malformed or incomplete backup
installation cannot run and systemd no longer ignores the intended guards.
