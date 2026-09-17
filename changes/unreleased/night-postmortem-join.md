---
title: Night light-activation post-mortem join over the intelligence ledger
target: internal
type: added
---

Adds `tools/night-postmortem-join.py`, a read-only report that lists every light that turned on overnight with the writer that did it (from the HA context the actuators already record), the brightness signature mapped to the generator's constants, the classifier's state at the time, whether a person turned it off again within a minute, and optional presence corroboration from an observer memory snapshot. It is the measuring instrument for the night-lighting fixes and the first step of the approved lighting plan; it changes no runtime behaviour.
