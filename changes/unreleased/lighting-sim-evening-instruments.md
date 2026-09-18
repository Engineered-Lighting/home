---
title: Evening (story T) instruments for the Living Lights simulation
target: internal
type: added
---

`tools/lighting-sim/analyze_evening.py` scores a simulated evening (18:00 to
01:00 by default; the window is a parameter) from pure functions over the
fake-light call list, the ambient switch calls, the recorder and the
per-minute snapshots: off-to-on turn-ons with automation attribution,
living-room turn-ons and brightenings while watching, time to dark, errand
route latency (the first turn_on that raises the zone light's level or
turns it on from off; a same-level tick re-set does not count), route
return latency (vacancy to the zone light at or below its pre-errand
level), route off latency and overshoot, unavailable-TV flicker counted on
level changes (`unavailable_flicker_raw` keeps every call), ambient strip
calls, the most lights on while watching, what was lit at fixed times and
tv_playing state changes. `watching_source="timeline"` scores an evening
from the inputs even when a belief was fed, so a stale belief cannot
create an artefact episode. Normalised call and snapshot digests
(`call_digest`, `snapshot_digest`, `digest_diff`) compare two runs
regardless of same-instant dispatch order, and every report carries a
`validity` block (`tv_measurable`; `is_scorable`) so unmeasurable recorded
evenings stay out of every score. Unit-tested without Home Assistant in
`tests/living_lights/test_analyze_evening.py`.

`tools/lighting-sim/test_sim_tv.py` adds the story T scenarios T1-T10 and
the story S extras S9-S13, each run with the belief toggle off and on, plus
recorded evenings from `LL_SIM_EVENINGS` (both variants, with the
evening's validity in the report). Override writes are counted per target
entity, so writes through `target: entity_id:` are seen (the M1 baseline's
S11 refusals: five 280-character writes, none accepted); empty-value
clears are reported apart. T7 scores its metrics from the timeline and its
toggle-on/off gate compares the normalised digests. A second gate,
`LL_SIM_ASSERT_TARGET=1`, asserts the plan's M2 acceptance and is expected
to fail on today's packages; `LL_SIM_ASSERT=1` asserts nothing new there,
so S1-S8 stay green. Every scenario always writes its report.

The M2 gates read the plan's acceptance paragraph literally, both toggles:
T3 compares every living-room light with the vacant target (the
`tv_vacant_floor_pct` default, 0, so dark) instead of looking for the old
8 % movie dim; T4 wants the office at its present target with the sofa
empty; T9 and T10 want the front-door and office zones at 0 % while the
sofa is occupied (the movie dim); T8 counts brightenings and ambient-strip
turn-ons inside each blip window (`brighten_in_blips`,
`brighten_in_blips_by_blip`, `ambient_on_calls_in_blips`) and wants zero
in both the 30 s and the 12 min blip; T7 compares the digests from the
heartbeat's death on; S9b runs twice more, the door camera reporting 30 s
before or 30 s after the phone (`S9b_real_arrival_door_first`,
`S9b_real_arrival_phone_first`), and wants the latch cleared within 10 min
of 03:00 either way; S10 counts override writes only before the first
occupancy; S12 wants zero override writes and no light above 30 % between
05:00 and 06:00 (`override_text_writes_0500_0600`,
`lights_above_30_0500_0600`, from the per-minute snapshot digest).

`tools/lighting-sim/compare.py` skips reports without the night metrics, so
a label directory holding story T, S9-S13 and evening reports next to the
night reports compares cleanly (`--labels old,new,m2`). `tests/__init__.py`
and `tests/living_lights/__init__.py` make
`python3 -m unittest discover -s tests/living_lights -t .` work.
