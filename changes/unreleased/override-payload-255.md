---
title: Lighting overrides fit the 255-character helper again
target: backend
type: fixed
---

Explicit lighting overrides were silently never landing. Both writers of
`input_text.living_lights_override_text_<zone>` - the good-morning energize
automation (about 315 characters) and the `set_presence_override` voice tool
(about 392 characters, with a nested baseline) - exceeded Home Assistant's
255-character input_text ceiling, and the refused write left the lights on
automatic. Both now write only the six keys the readers use (`command_id`,
`brightness_pct`, `hold_until`, `vacancy_grace_s`, `pinned`, `source`; about
160 characters even with a far-future timestamp). The colour temperature,
prompt, baseline and audit fields move to the command-ledger JSONL row, the
tool result and the good-morning logbook line, and the function file refuses
an oversize payload with a clear error before any write (`MAX_INPUT_TEXT`).
Deploy: copy `homeai_good_morning.yaml` and
`extended_openai_conversation/functions/living_lights.py` with a `.bak.<ts>`
of each, `ha core check`, reload automations, and restart core for the
function file. Rollback: restore the `.bak.<ts>` copies.
