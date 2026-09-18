---
title: Replay recorded evenings into the Living Lights simulation
target: internal
type: added
---

`tools/lighting-sim/replay.py --evenings` writes one replayable input file
per calendar evening (18:00 to 01:00 next day) next to the night files.
Occupancy comes from the ledger classifier states as before; the TV, the
asleep latch and the at-home flag come from the observer memory snapshot's
change rows when the observer was alive across the whole window, from the
ledger's event-sampled flags otherwise, and are marked unmeasurable when
neither source can vouch for them. Each file carries a validity block
(`tv_source`, `tv_measurable`) so the TV-watching stories only score on
evenings whose TV state is real.
