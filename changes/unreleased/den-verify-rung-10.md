---
title: Den verify tool for scene changes (rung 10)
target: internal
type: added
---

`tools/den-verify` adds rung 10: after each scene change on the den's Hue
bulbs it checks the next settled metering frame against the light model's
prediction for the state the bulbs actually reached (±15%, hit rate at least
85%), writes `Decision.verify` and the `LightState` residual, and reports which
misses daylight, a shadow or drift explain. Under the proposed read-frame
background, a held or superseded bulb that stayed put cannot make a miss, and a
free bulb's fault still shows beside it: such a point counts both ways unless the
decision's own bulbs could not show there. Dims and
offs are judged to the wider of 15% of the level and an allowance on the bulbs
that changed, and every level check carries the meter's noise, so a perfect dim is
a hit and a bulb that stayed on is a miss. A fault inside those bounds cannot be
seen: at the proposed allowance, one bulb of four stuck on a dim shallower than
about 2.5:1 reads as a hit, the same line as going up, and the report gives what
the hit rate would be without such hits. A change below the meter's noise is judged
to that noise, and a pass needs the hit rate without such points to pass too. A
decision that read a frame from before a step landed, or from before a person changed
a light, is unverifiable under the read-frame background, not a hit or a miss. Which
points count never depends on what they read. It reads rung 8 Stage 1's real model
through den-light's own `predict.py`, pinned by hash in the report, and rung 9's
`decisions.jsonl` and, with `--surfaces`, `surfaces.json`. With no `predict.py` beside
it a model is refused, and a scored run on a model marked fake is refused, since
plumbing is never a gate. A decision counts as verified only when something was judged;
the rest are listed with why (no read frame, no frame after settling, no surface map,
no `applied_scene`, unknown state), and the first console line gives both counts. A lit
bulb with no level is unknown, never 0. A miss with no window data or no occupancy file
is not called drift, and no hit or miss moves for it. Several evenings in one run give
the session-bootstrap interval. Every file it reads is refused with one sentence and
exit 2 when it is UTF-16 (what PowerShell 5.1's `>` writes), missing, malformed or the
wrong shape, down to what is inside a line, and a model error on one decision writes
`verify` null with the reason and the run goes on. It reads records
already on disk, opens no network connection, and refuses a scored run until the
owner closes its open slots in `VERIFY-V1.md`.
