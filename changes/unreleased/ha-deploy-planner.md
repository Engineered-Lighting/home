---
title: Offline deploy planner for Home Assistant package files
target: internal
type: added
---

`tools/ha-deploy-packages.py --plan` writes the deploy checklist, a receipt
skeleton and staged copies for a set of Home Assistant package files without
contacting the host. It records each file's working-tree and base (`origin/main`)
sha256, byte size and host target, lists every automation with its
alias-derived entity id (`automation.<slug>`, what Home Assistant assigns at
first registration; automations whose alias slug differs between base and
after are flagged as renamed, since the entity registry keeps the first id),
every script and every `input_boolean`/`input_number`/`input_text`/
`input_datetime`/`input_select` helper with its declared initial value (applied
when the helper is new on the host or on a core restart, not on an
`input_*/reload` of an existing helper), and flags a core restart only when a
custom-component Python file is in the set. Top-level domains the planner does
not handle (`mqtt`, `shell_command`, ...) are recorded per file and warned about
in the checklist header, as are aliases whose characters the slugify dropped.
Absolute `--files` paths are resolved against the repository and refused when
outside it. The checklist follows the story-S deploy precedent: hash compare,
partial backup, `.bak.<ts>` copies, scp, hash verify, `ha core check`, REST
reloads, post-checks (with a `GET /api/states` fallback filtered by
`attributes.id` for renamed automations) and receipt, with host and token
placeholders only.
