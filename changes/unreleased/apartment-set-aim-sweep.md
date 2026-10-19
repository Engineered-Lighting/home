---
title: Beam sweep when an aim is set in the Apartment
target: internal
type: added
---

The Apartment renderer can now move a virtual fixture's warm beam to a newly set aim with a 650 ms sweep, instantly under reduced motion. Only the drawing animates; the aim, readback and controls take the new aim at once. The host opts in when Set aim is pressed, so nothing changes until the aim wiring uses it.
