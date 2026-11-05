# den-verify (rung 10)

After each scene change on the den's Hue bulbs, did the light land where rung
8's light model said it would? `VERIFY-V1.md` says what is scored, on which
frames, and which numbers are still open. This folder only reads records that
are already on disk and writes results next to them: it opens no network
connection and never talks to a camera, the Jetson, LA, Home Assistant or a
light.

Python 3.8 or later, standard library only.

| File | Does |
|---|---|
| `VERIFY-V1.md` | The design note and its one `verify-v1` block of numbers and open slots. |
| `method.py` | Reads that block (CRLF or LF), lists open slots and the pending decisions. |
| `records.py` | JSON Lines in and out, clock strings, LightState ids, schema field names. |
| `settle.py` | The settle rule: den_lighting's 2 s fades (5 s on a restore), probes at 0 s. |
| `light_model.py` | The one adapter to rung 8's model; every prediction goes through it. |
| `fake_model.py` | rung 8's prediction rule from its plan, until Stage 1's `predict()` exists. |
| `score.py` | `Decision.verify`, `LightState.residual` and the report. |
| `verify.py` | The command line. |
| `synthetic.py`, `test_verify.py` | Synthetic fixtures from `schemas.json` and the tests. |

## Commands (PowerShell 5.1)

From the repo folder. The tests:

```powershell
python -m unittest discover -s tools\den-verify -p "test_*.py"
```

What is still open:

```powershell
python tools\den-verify\verify.py slots
```

A preview on the proposed values, which writes only `report-preview.json`:

```powershell
python tools\den-verify\verify.py run --decisions C:\den-verify\s1\decisions.jsonl --light-states C:\den-verify\s1\light_states.jsonl --corrections C:\den-verify\s1\corrections.jsonl --model C:\den-light\light-model.json --out C:\den-verify\s1\preview --preview
```

A scored run is the same command without `--preview`, into a new `--out`
folder; it refuses while `VERIFY-V1.md` has an open slot. Optional inputs:
`--occupancy` and `--gold` (formats in `VERIFY-V1.md`), and `--utc-offset-h -7`
when the records' clock strings are not in this PC's time zone. Run folders
stay on the PC: no records, frames or reports go into git.

The console gives the verdict, then how many points were left out of the hit
rate (lit by held or superseded bulbs where the decision's own bulbs could not
show), how many counted points have a change below the meter's noise, with the
hit rate without them, which must pass too, and how many hits are beyond 15% of the
level, on dims and offs, with the hit rate if those had missed. `VERIFY-V1.md` gives
each rule. Which points count never depends on what they
read. A run with nothing left to count reads `insufficient`.

Running on real records needs the owner's go-ahead, and the records come from
rungs that have not run yet (rung 7's metering frames, rung 8's model, rung 9's
decisions).
