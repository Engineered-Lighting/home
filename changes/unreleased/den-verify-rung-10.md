---
title: Den verify tool for scene changes (rung 10)
target: internal
type: added
---

`tools/den-verify` adds rung 10: after each scene change on the den's Hue
bulbs it checks the next settled metering frame against the light model's
prediction for the state the bulbs actually reached (±15%, hit rate at least
85%), writes `Decision.verify` and the `LightState` residual, leaves held and
superseded bulbs out of misses, and reports which misses daylight, a shadow or
drift explain. It predicts with rung 8 Stage 1's own `predict()` over the
session's `light-model.json`. It reads records already on disk, opens no
network connection, and refuses a scored run until the owner closes its open
slots in `VERIFY-V1.md`.
