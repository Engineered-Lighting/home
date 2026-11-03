---
title: Replay the den bulbs' recorded on/off states in den-core
target: internal
type: added
---

Adds `--lights` to `tools/den-core/run.py`. It reads the four den bulbs' states
exported read-only from the HA recorder, so a replay of a real evening sees which
bulbs were on, and labels each change as den_lighting's own, a person's, or
unknown. HA's recorder keeps no light brightness or colour temperature, so the
states are on/off only, and the comparison with den_lighting's live commands now
treats a missing level as matching. Nothing contacts a host or sends a light
command.
