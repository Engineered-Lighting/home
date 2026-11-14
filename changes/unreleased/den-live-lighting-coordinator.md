---
title: den-live runs the den lighting coordinator
target: backend
type: added
---

den-live now runs the den lighting coordinator live, as its own loop observer. It turns on only when den-live's settings
file has a `coordinator` block (`lights`: den_lighting's room lights; optional `utc_offset_h`, entity names and
`fixtures_file`), and it uses the Qwen ask's Home Assistant link: the same `den-live` user and `ha-token.txt` credential.

- **What it reads:** each processed frame's confirmed tracks and their room joints. About once a second, over
  Home Assistant's REST API: den_lighting 1.1's status sensor (occupancy, the game session, the owner's latest
  correction) and the "Den lighting coordinator" switch.
- **What it sends:** `den_lighting.coordinator_plan` for the lights it has learned something for. A real fixture's
  call goes to the Home Assistant service its row names. Every fixture call is written to `fixture_calls.jsonl`, sent or
  not.
- **What it keeps:** Decisions, plans, corrections and kept marks, in day files under `days/<date>/` (14 days).
  `learned.json` and `settings.json` go under `coordinator/`.
- **Its routes on the people stream** (`/api/den-live/v1/homes/den/coordinator`):
  - GET: state;
  - POST `switch`, `settings`, `learned/clear` and `desk`.

  The stream now answers POST for its own routes only. Any other POST still gets 501.
- **The pinned core:** `tools/den-core` at 11f694de, and the lighting brain's aim files, as the pinned
  `coordinator` tree. The release scan allows the brain's den fixture ids for that one blob.

Its failure never stops the markers, and it's off without the block.
