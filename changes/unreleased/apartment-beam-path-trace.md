---
title: Beam path trace for the Apartment aim chip
target: internal
type: added
---

The Apartment renderer can now trace a straight ray from a light to an aim point and report the surface normal, the first blocker with a plain name such as "a wall", and whether the aim is grazing. This is what the aim chip needs for "Blocked by …" and the grazing note. Nothing uses it yet; existing picking results are unchanged.
