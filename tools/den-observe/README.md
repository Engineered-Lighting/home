# den-observe: rung 2, Observations in the room frame

den-observe turns detector output plus depth into `observation` records
(`docs/architecture/live-v1/schemas.json`), in the room frame, with
covariance, depth timing, a declared plane fallback and freshness.
[METHOD.md](METHOD.md) says how each value is made and holds every number,
including the slots the contracts leave open.

| File | Does |
|---|---|
| `replay.py` | Replays an R take with detector rows or the fake detector; `--demo` runs on synthetic data. |
| `observe.py` | The writer: one detector row and its take frame give Observations. |
| `roommodel.py` | Reads rung 1's `room-model.jsonl`, resolves `transform_id` to one camera entry, and refuses a missing, stale or invalidated registration. |
| `detections.py` | The `den-detections-1` row format, and the adapter from perception-lab's results. |
| `fake_detector.py` | A scripted stand-in for YOLO26s + RTMPose-m. It never looks at pixels. |
| `takes.py` | Reads an R take's depth plane, frame times and clock (through den-take's reader). |
| `lift.py`, `camgeom.py` | Depth lifting and camera math, following perception-lab without numpy. |
| `method.py` | Reads METHOD.md's block and refuses a scored run while a slot is open. |
| `textfile.py` | Reads a text file as UTF-8, with or without a BOM, and refuses UTF-16 or anything else by name. |
| `synth.py` | Synthetic rooms, takes and camera entries with known geometry, for the tests and the demo. |
| `testdata/` | A copy of the file den-register writes on synthetic data, and the script that makes it. |

It needs Python 3.12 and the standard library only: nothing to install, no
numpy. It reads files on this PC and never contacts a camera, the Jetson, LA,
Home Assistant or any other host. It runs no detector, uses no GPU and sends
nothing to the lights.

## Privacy rules

- Observations of a real take stay on this PC, next to the take (for example
  `C:\den-takes\<id>\observe-1\`). `replay.py` refuses an `--out` inside a git
  checkout or OneDrive, and never overwrites a folder that holds observations.
- An agent may read `run.json` (counts, slots and the gate) but not
  `observations.jsonl` or any frame.

## Step 1, once: a folder for this branch

As for den-take, leave your checkout at `C:\claude\home` as it is and add a
worktree for `claude/rung-2-observe`. In PowerShell 5.1:

```powershell
git -C C:\claude\home fetch origin claude/rung-2-observe
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\rung-2 claude/rung-2-observe
Set-Location C:\claude\home\.claude\worktrees\rung-2
```

Run every command below from that folder (the one that holds `tools\`). If
`python` is not found, use `py -3.12` instead.

## The demo and the tests, any time

```powershell
python -B tools\den-observe\replay.py --demo
```

It builds a synthetic take, room and detector rows in a new folder under your
temp folder, prints that folder, writes `observe\observations.jsonl` and
`observe\run.json` there, and ends with `UNSCORED` and the open slots. Delete
the folder afterwards with `Remove-Item -Recurse -Force` and the printed path.

```powershell
python -B -m unittest discover -s tools\den-observe -v
```

The tests use synthetic data only; the one network guard allows 127.0.0.1
and nothing connects. They take about 15 seconds. When `tools\den-register` is
in the same checkout (or `$env:DEN_REGISTER_DIR` names it), they also run rung
1's own synthetic fit and read what it writes; otherwise those tests are skipped
and the copy in `testdata` stands in for it.

## A real take, once rung 1 has registered the camera

Rung 1 (`tools\den-register`) writes `C:\den-register\room-model.jsonl`, one
revision per line. den-observe reads its last line, which holds the camera's
current registration. It refuses the run if rung 1's mount check has invalidated
that registration, or if the take is older than the take that registration came
from: register again, or use a newer take. The plumbing run with the fake
detector is:

```powershell
python -B tools\den-observe\replay.py --take C:\den-takes\2026-11-10-1 --room-model C:\den-register\room-model.jsonl --fake --out C:\den-takes\2026-11-10-1\observe-1
```

The fake detector's boxes are not where anyone is; this checks the path, not
the geometry. With real detector rows (the detectors run only on the owner's
go-ahead, and write `den-detections-1` through `detections.from_perception_lab`),
use `--detections <rows file>` instead of `--fake`.

Exit codes: 0 written, 1 scored and the gate failed, 2 refused (nothing
written).

In PowerShell 5.1, don't make a file this tool reads with `>`: that writes
UTF-16, which is refused (`--out` writes the tool's own output). To repair one:
`Get-Content rows.jsonl | Set-Content -Encoding utf8 rows-utf8.jsonl`.

## Closing a slot, then a scored run

A slot is one line in METHOD.md's `observe-v1` block, for example

```text
detector_max_age_s = open     # open, proposed 1.0 (R5 baseline: 115 of 228 expired, about 1.46 s)
```

To set it, replace `open` with the number (or, for a form, the quoted text)
and keep the note: `detector_max_age_s = 1.0     # set 2026-11-.., R5 baseline ...`.
When no `open` is left, add `--scored` to the command above with
`--detections`. A scored run also needs the take's newest check to PASS with
a read start slate (its clock) and `available_ts` on every detector row.

It also needs rung 1's mount check to have confirmed the registration on this
take or a later one, because only that says the camera had not moved. So after
the takes you want to score, follow "Later: has the camera moved?" in
`tools\den-register\README.md`, then run the scored replays. Without that the
run is refused and says so. An unscored run is written with a `mount: NOT
CONFIRMED` line.
