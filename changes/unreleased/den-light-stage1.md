---
title: Den light model Stage 1 tools (rung 8)
target: internal
type: added
---

`tools/den-light` adds rung 8's Stage 1: a probe session that pauses
den_lighting, steps the four den bulbs in the empty den with the camera held,
logs every write as a non-human change with its send and settle times, and
writes the saved normal scene back before resuming; a model that fits each
bulb's contribution per camera block and scores it on held-out scenes; and a
pure `predict(scene, model)` over `light-model.json` for the coordinator and
verify. Built and tested on fakes only; nothing runs until the owner approves.
