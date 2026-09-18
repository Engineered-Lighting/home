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

New packages (this checkout) with assertions, plus every recorded night:

    LL_SIM_LABEL=new LL_SIM_ASSERT=1 LL_SIM_NIGHTS=~/vjepa-home/experiments/lighting-sim/nights \
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim.py -o asyncio_mode=auto -p no:cacheprovider -q

Old packages (`git archive origin/main ha-config | tar -x -C <dir>`), no assertions:

    LL_SIM_LABEL=old LL_SIM_PACKAGES=<dir>/ha-config LL_SIM_NIGHTS=... \
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim.py -o asyncio_mode=auto -p no:cacheprovider -q

Comparison table:

    python3 tools/lighting-sim/compare.py --labels old,new

Environment: `LL_SIM_STEP_S` (default 20 s near input changes, 60 s in quiet
stretches), `LL_SIM_HOURS` (truncate scenarios for diagnostics),
`LL_SIM_REPORT` (report root).

## Scenarios

S1 evening then bed, S2 TV left on, S3 four-minute night excursion, S4
sleeping late on a weekday, S5 leaving at 23:00, S6 guest on the sofa all
night (known limitation, report only), S7 stuck occupancy sensor (known
limitation, report only), S8 the 2026-09-13 pattern that produced the old
tick-path false latch. Recorded nights are parametrized from the night files.
