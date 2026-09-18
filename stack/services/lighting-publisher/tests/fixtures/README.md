# Test fixtures

Synthetic fixtures only. `tv_evening.json` and `quiet_night.json` are the
golden `lighting-beliefs-state/v1` packets for the `lighting_beliefs`
tests; they contain no household data.

## Recorded nights are never in the repository

`tests/test_estimator.py::RecordedNights` replays real nights through the
asleep estimator. Those files (schema `living-lights-sim-night/v1`, written
by `tools/lighting-sim/replay.py`: `initial`, `events[{t, entity, state}]`,
`actual`) are household data and live under
`~/vjepa-home/experiments/lighting-sim/nights` (mode 0600), never here.

Run the replay by naming that directory:

```bash
LL_PUB_FIXTURES=~/vjepa-home/experiments/lighting-sim/nights \
  python3 -m unittest tests.test_estimator.RecordedNights -v
```

Without `LL_PUB_FIXTURES` the test skips. The replay ticks once a minute and
feeds camera-level Frigate occupancy with no observer (stale, so Frigate
alone counts). It asserts two things.

Every exit from `likely_asleep` has an allowed reason: occupancy, arrival,
wake or brighten command, manual flip, departure.

And the latch times match the simulator's, which is this milestone's
acceptance bar. With `LL_PUB_SIM_REPORTS` pointing at the simulator's own
night reports, every simulator latch these fixtures can express is matched
by a publisher latch within five minutes. Expressible means at or after
midnight: the fixtures carry Frigate occupancy, the television and the
phone, but no `sensor.living_lights_profile`, and the legacy night window
opens at 22:30 only through that sensor, so a pre-midnight latch is not a
disagreement this replay can measure. Those are counted and named, never
asserted on. A simulator latch may also be met by a run of `likely_asleep`
the publisher had already entered, but only on a night that had such an
unexpressible latch, only from a run opening after it, and only inside the
legacy re-arm window; the number of latches met that way is pinned, so the
licence cannot widen unnoticed. Regenerating the fixtures with the profile
sensor is the honest way to widen the bar.
