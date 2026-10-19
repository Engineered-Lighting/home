---
title: Perception Lab HA source gains a Den-area allowlist and owner home/away
target: backend
type: added
---

The `perception_lab_source` Home Assistant component (0.2.0) adds two opt-in
modes. Area mode builds its allowlist from HA areas (for example the Den), with
a domain filter and explicit include/exclude. Presence mode exposes a single
person only as `home`/`away`/`unknown`. A new list endpoint returns the allowed
entity ids with a revision digest. The Lab can read that list at session start,
and it can poll a second, independent HA source for owner home/away. Each
source keeps its own ledger stream. Existing exact-list configurations are
unchanged, and every HA config change still needs an HA restart.
