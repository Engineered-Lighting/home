---
title: Victoria Lab occupancy gate reads the occupancy sensor in memory and wakes on an edge
target: backend
type: added
---

An optional `occupancy_reader` block in the Lab controller's live config polls the den person-occupancy sensor at 2 Hz over HTTPS and keeps the reading in memory with the same freshness, hold (default 10 s, configurable 3-15 s), grace and fail-closed rules, logging only on/off edges; it is used only when its entity is the HA source's bound camera-derived occupancy entity (otherwise the pumps and the JEV reasoning cadence keep the recording's HA-row gate, with one warning line), and a live pump block naming a different occupancy entity, hold or grace is refused. The live Qwen and V-JEPA pumps prefer it over the recording's HA rows and wake at once when the room becomes occupied, still checking occupancy before any LA call; without the block nothing changes.
