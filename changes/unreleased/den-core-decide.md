---
title: Add the offline shadow decision engine skeleton for the den bulbs
target: internal
type: added
---

Adds `tools/den-core/decide.py`, an offline, shadow-only policy for the four den Hue bulbs. It reads PersonState, occupancy and den_lighting's mode and holds, and writes Decision records with four scored candidates, held bulbs pinned at their read-back values and the skipped channels. It never sends a light command or reaches a host, and it does nothing on unknown occupancy or during a game. Its only difference from den_lighting is a labelled placeholder reading look on the free bulbs while reading is engaged; the look's real values are still the owner's choice.
