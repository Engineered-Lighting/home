---
title: Count den_lighting's hand corrections per occupied hour from recorder exports
target: internal
type: added
---

Adds `tools/correction-capture`, an offline tool that reads three read-only Home
Assistant recorder exports (den_lighting's status sensor, the four den bulbs'
states and den_lighting's commands) and counts hand corrections per occupied
hour, in game and no-game time. It counts two ways, people only and every hold,
each with an exact Poisson interval and the weeks a 30% improvement would take
to show. It writes `exposure.json` outside every checkout, with a planning
verdict from the rule `cph-budget-v0`, which was pinned before any real export
was opened. The README gives the owner's PowerShell export commands. Export by
about 2026-11-07, before the recorder drops den_lighting 1.0.4's first
evenings. Nothing contacts a host or sends a light command.
