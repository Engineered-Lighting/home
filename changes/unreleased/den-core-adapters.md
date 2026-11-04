---
title: Replay real den evenings with journal occupancy and compare with den_lighting's live commands
target: internal
type: added
---

Adds two offline file adapters to `tools/den-core`. `--journal` reads the
Jetson occupancy reader's journal edges, so replays of copied fact logs use the
den's real occupancy instead of assuming the room is occupied. It prints each
span where the journal hides an off the reader never logged, since the replay
stays occupied there. `--live-commands` reads `den_lighting_command` events
exported read-only from the HA recorder, and the run summary compares them,
bulb by bulb, with what the replayed den_lighting last acted on. The live commands are only a reference: no
policy sees them, and den_lighting's code is unchanged. The README gives the
owner's copy and export commands. Nothing contacts a host or sends a light
command.
