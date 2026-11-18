---
title: The den spotlight's codec and simulator are pinned to the firmware with the timed MOVE
target: internal
type: changed
---

den-live's FC13 codec, its trace header and the replay move from firmware `cf1c325` to `62ea043`, which adds the timed MOVE (slower moves and spot fades), at-rest MOVEs, joining a flight and a capabilities attribute.
- **The pins:** a new private codec bundle (a `git archive` of that commit) and a host simulator built from its own trees. The trace's run header names the new firmware, codec and simulator.
- **The timed MOVE:** den-live's encoder takes `timed: {min_duration_ms, fade_ms, fade_curve}` and makes the firmware's own timed vectors byte for byte. The replay checks a timed MOVE's fields.
- **Only where it's understood:** the sender sends a timed MOVE only to a board whose own capabilities (from `c12.get_profile`) say so. Firmware before v2-b gets none.
- **Commissioned replays:** without a record, the new simulator answers every MOVE `needs_commissioning`, which keeps each run's replay byte-exact. `c12_replay.py --sim-record` commissions it with a board's own record, so a desk run's answers can be matched.
