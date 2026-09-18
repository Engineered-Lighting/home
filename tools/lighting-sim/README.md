# Living Lights simulation

Runs the real Home Assistant packages (`ha-config/packages/*.yaml` plus
`ha-config/homeai_proactive.yaml`) inside a Home Assistant core test instance
with a fake clock and a fake light domain, and drives them with either
scripted nights or nights reconstructed from the intelligence ledger. It
exercises the deployed artifacts, not a re-implementation of them.

## What it proves and what it cannot

- Proves: given a sequence of Frigate occupancy, TV and presence states, what
  the automations, template sensors and pilots do, minute by minute, on the
  exact YAML that will be deployed. Old and new packages run on the same
  inputs, so the comparison is apples to apples.
- Cannot prove: how the sensors behave. Recorded nights carry real occupancy
  timing (reconstructed from the classifier states the ledger holds, which
  include the 90 s stable hold), but the raw motion sensors were never
  recorded and are replayed as camera-level person occupancy. That flatters
  the old package's motion blockers, never the new one, which ignores them.
- Runs at a newer Home Assistant core (2026.9) than the host (2026.7); the
  YAML uses the 2024.10+ syntax that both accept.

## Setup

    uv venv --python 3.14 ~/.venvs/ha-sim
    uv pip install --python ~/.venvs/ha-sim/bin/python pytest-homeassistant-custom-component pyyaml

## Run

Recorded nights (private; written under `~/vjepa-home/experiments/lighting-sim/`):

    python3 tools/lighting-sim/replay.py --since 2026-09-02 --until 2026-09-17

Recorded evenings (18:00 to 01:00 next day, one file per calendar date, under
`~/vjepa-home/experiments/lighting-sim/evenings/`). The TV, asleep and at-home
states come from the observer memory snapshot's change rows when the observer
was alive across the whole window, else from the ledger's event-sampled flags;
`validity.tv_measurable` says whether the evening can score a TV story:

    python3 tools/lighting-sim/replay.py --evenings --since 2026-09-02 --until 2026-09-17 \
      --memory-snapshot /srv/data/vjepa-home-snapshots/2026-09-17/memory.sqlite3

New packages (this checkout) with assertions, plus every recorded night:

    LL_SIM_LABEL=new LL_SIM_ASSERT=1 LL_SIM_NIGHTS=~/vjepa-home/experiments/lighting-sim/nights \
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim.py -o asyncio_mode=auto -p no:cacheprovider -q

Old packages (`git archive origin/main ha-config | tar -x -C <dir>`), no assertions:

    LL_SIM_LABEL=old LL_SIM_PACKAGES=<dir>/ha-config LL_SIM_NIGHTS=... \
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim.py -o asyncio_mode=auto -p no:cacheprovider -q

Comparison table:

    python3 tools/lighting-sim/compare.py --labels old,new

Story T evenings (T1-T10), story S extras (S9-S13) and recorded evenings,
each scenario with the belief toggle off and on. `LL_SIM_ASSERT=1` asserts
nothing here; `LL_SIM_ASSERT_TARGET=1` asserts the plan's M2 acceptance and
is expected to fail on today's packages (the reports are the baseline):

    LL_SIM_LABEL=m1tv LL_SIM_ASSERT_TARGET=1 LL_SIM_EVENINGS=~/vjepa-home/experiments/lighting-sim/evenings \
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim_tv.py -o asyncio_mode=auto -p no:cacheprovider -q -s

Evening metrics come from `analyze_evening.py` (pure functions; unit tests
in `tests/living_lights/test_analyze_evening.py`).

Environment: `LL_SIM_STEP_S` (default 20 s near input changes, 60 s in quiet
stretches), `LL_SIM_HOURS` (truncate scenarios for diagnostics),
`LL_SIM_REPORT` (report root).

## Scenarios

S1 evening then bed, S2 TV left on, S3 four-minute night excursion, S4
sleeping late on a weekday, S5 leaving at 23:00, S6 guest on the sofa all
night (known limitation, report only), S7 stuck occupancy sensor (known
limitation, report only), S8 the 2026-09-13 pattern that produced the old
tick-path false latch. Recorded nights are parametrized from the night files.

Evenings (`test_sim_tv.py`, 18:00 to 01:00 on a Sunday): T1 watch, errand to
the sink, return; T2 cooking 40 min while the TV plays; T3 TV on, nobody for
2 h; T4 laptop at the office desk with the TV on; T5 nap (belief napping at
22:30); T6 errand becomes departure; T7 publisher dead from 21:00 (belief
stale); T8 lg_tv unavailable 30 s and 12 min; T9 front door 2 min during a
film; T10 office 30 min while another person watches. Nights (22:00 to
09:00): S9 presence reconnect at 03:00; S9b real arrival at 03:00; S10 late
sleeper on a weekday; S11 energize (wake at 07:30, override writes counted
with their lengths); S12 10-min kitchen excursion at 05:00; S13 owner clears
the latch by hand at 02:00. Both feed the level-0 tv_watching oracle and the
publisher heartbeat; the "belief_on" variant also turns
`input_boolean.living_lights_actuate_from_belief_changes` on.
