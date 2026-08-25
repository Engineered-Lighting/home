---
title: Fix the identity provisioning services failing to start
target: backend
type: fixed
---

The `provision-identity-binding-kernel-role` and
`provision-parent-relationship-kernel-role` services declared their tmpfs mount
as an unquoted YAML flow sequence, so the comma inside the options split it into
two entries and Docker rejected the second as a mount path: `invalid mount path:
'mode=1777' mount path must be absolute`. Neither service could start. Both
entries are now quoted, and every other service already used the block style
that is unaffected.
