---
title: Replay den evenings on den_lighting's own holds and occupancy, and score the coordinator on replay
target: internal
type: added
---

Adds two offline file adapters to `tools/den-core/run.py`. `--corrections`
reads correction-capture's `corrections.jsonl`, so the replayed den_lighting
holds each bulb a person changed, as the live one did, and the comparison with
den_lighting's live commands compares like with like. `--status` reads
den_lighting's own status sensor export for its occupancy, so one Home
Assistant export replays an evening on the occupancy den_lighting itself used;
time the live den_lighting spent in a game or switched off is left out of that
comparison. Existing replays are unchanged byte for byte. Nothing contacts a
host or sends a light command.

It also extends den-core's decide into rung 9's coordinator v0, scored on
replay. Every decision now records its applied scene, which bulbs serve whom
through the light model, the fixed Look and Preference stand-ins, and its hard
constraints, and `score.py` scores it against the frozen den_lighting with
both run through the same light model. Rung 8's model is not built yet, so a
fake built from its plan stands in, and `score.py` refuses a scored run while
the model is fake or any number the contracts leave open is unset. The hold
fixtures run under each compensation option, none admitted until the owner
decides.
