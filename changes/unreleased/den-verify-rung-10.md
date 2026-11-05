---
title: Den verify tool for scene changes (rung 10)
target: internal
type: added
---

`tools/den-verify` adds rung 10: after each scene change on the den's Hue
bulbs it checks the next settled metering frame against the light model's
prediction for the state the bulbs actually reached (±15%, hit rate at least
85%), writes `Decision.verify` and the `LightState` residual, and reports which
misses daylight, a shadow or drift explain. A point where a held or superseded
bulb gives a large share of the light is never a hit, and is a miss only when
that bulb could not account for it, so a hold cannot lift a failing session to a
pass. A deep dim or off whose change landed stays unverifiable, because its
level cannot be judged, and a bulb that did not make the change is still a miss.
It reads records already on disk, opens no network connection, and refuses a
scored run until the owner closes its open slots in `VERIFY-V1.md`.
