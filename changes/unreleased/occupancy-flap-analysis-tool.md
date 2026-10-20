---
title: Occupancy flap analysis from the Jetson edge log
target: internal
type: added
---

`tools/la-latency/occupancy_flaps.py` reports den occupancy ON stretches, short OFF gaps and short ON blips per window, normalised per occupied hour, from the occupancy reader's edge log. It is used to compare Frigate detection changes before and after.
