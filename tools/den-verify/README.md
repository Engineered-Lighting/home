# den-verify (rung 10)

After each scene change on the den's Hue bulbs, did the light land where rung
8's light model said it would? `VERIFY-V1.md` says what is scored, on which
frames, and which numbers are still open. This folder only reads records that
are already on disk and writes results next to them: it opens no network
connection and never talks to a camera, the Jetson, LA, Home Assistant or a
light.

Python 3.8 or later, standard library only. Predictions come from rung 8
Stage 1's `predict()` over the session's `light-model.json`, so
`tools/den-light` must sit beside this folder, as it does in the repo.

| File | Does |
|---|---|
| `VERIFY-V1.md` | The design note and its one `verify-v1` block of numbers and open slots. |
| `method.py` | Reads that block (CRLF or LF), lists open slots and the pending decisions. |
| `records.py` | JSON Lines in and out, clock strings, LightState ids, schema field names. |
| `settle.py` | The settle rule: den_lighting's 2 s fades (5 s on a restore), probes at 0 s; and when a read frame is stale. |
| `light_model.py` | The one adapter to rung 8's model: every prediction goes through it, to rung 8 Stage 1's `predict()` in `tools/den-light/predict.py`. |
| `score.py` | `Decision.verify`, `LightState.residual` and the report. |
| `verify.py` | The command line. |
| `synthetic.py`, `test_verify.py` | Synthetic fixtures from `schemas.json` and the tests. |

## Commands (PowerShell 5.1)

**Don't redirect with `>` in PowerShell 5.1.** It writes UTF-16, which `verify.py`
refuses by name with exit 2 on every file it reads. Let a tool write its own output
file, or repair a file with
`Get-Content bad.jsonl | Set-Content -Encoding utf8 good.jsonl`. It reads the
byte-order mark and CRLF that writes.

**What it refuses.** Any input that is missing, not JSON or the wrong shape, down to
what is inside a line, is one sentence naming the file and the line, and exit 2,
never a traceback. So is a line of den-core's `log.jsonl` given as `--decisions`:
pass the `decisions.jsonl` beside it. Anything no check named still ends in one
sentence and exit 2: `verify.py stopped on an input of the wrong shape, or a bug of
its own`, with the exception and where it happened.

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
show), how many decisions read a frame from before a step landed, so nothing taken
from that frame was judged, how many counted points have a change below the meter's noise, with the
hit rate without them, which must pass too, and how many hits are beyond 15% of the
level, on dims and offs, with the hit rate if those had missed. `VERIFY-V1.md` gives
each rule. Which points count never depends on what they
read. A run with nothing left to count reads `insufficient`.

Running on real records needs the owner's go-ahead, and the records come from
rungs that have not run yet (rung 7's metering frames, rung 8's model, rung 9's
decisions).
