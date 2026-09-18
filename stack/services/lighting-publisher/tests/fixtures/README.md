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

Without `LL_PUB_FIXTURES` the test skips. The replay ticks once a minute,
feeds camera-level Frigate occupancy with no observer (stale, so Frigate
alone counts), and asserts only that every exit from `likely_asleep` has an
allowed reason (occupancy, arrival, wake or brighten command, manual flip,
departure); the recorded latch transitions are printed for comparison, not
asserted, because the legacy latch and the estimator follow different rules.
