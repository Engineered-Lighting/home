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
