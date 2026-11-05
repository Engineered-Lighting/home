---
title: Replay den evenings from Home Assistant exports with den_lighting's own holds and occupancy
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
