# den-track: Tracks from geometry, and E2's scorer (rung 3)

Rung 3 of the live architecture (`docs/architecture/live-v1/ladder.json`).

- **Reads:** `observation` records (rung 2 writes them).
- **Writes:** `track` records (rung 5 reads them): one anonymous person followed
  through time, replacing today's per-frame `slot-N` ids (note R7).
- **Scores:** E2's geometry-only arm, per crossing: silent swaps, IDF1 and the
  one-sided bound.

**Status: built, not run.** Everything here is tested on synthetic scenes. It reads
and writes files on this PC and never contacts a camera, the Jetson, LA, Home
Assistant or any other host. It needs Python 3.12 and the standard library: nothing
to install.

[METHOD.md](METHOD.md) says how each value is made and holds every number.

| File | What it is |
|---|---|
| `den_track.py` | The command line |
| `METHOD.md`, `method.py` | The numbers, the open slots, and the reader that refuses a scored run while one is open |
| `records.py` | `observation` lines to frames; tracker states to `track` lines |
| `tracker_core.py`, `associate.py`, `kalman.py` | The tracker: causal and deterministic, on plain inputs |
| `truth.py` | The ground-truth format, `den-track-truth/1`, which rung 4's labeller will write |
| `score.py`, `scorer_core.py`, `scorer_math.py` | E2's scorer |
| `synth.py` | Synthetic crossings, occlusions, near passes and noisy depth, for tests and tuning |
| `test_*.py` | The tests (standard library `unittest`) |

## Commands (PowerShell 5.1)

As for the other rungs, leave your checkout at `C:\claude\home` as it is and add a
worktree. Run everything from that folder, the one that holds `tools\`. If `python`
is not found, use `py -3.12`.

```powershell
git -C C:\claude\home fetch origin claude/rung-3-track
```

```powershell
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\rung-3 claude/rung-3-track
```

```powershell
Set-Location C:\claude\home\.claude\worktrees\rung-3
```

The tests take about 5 seconds, and connect to nothing:

```powershell
python -B -m unittest discover -s tools\den-track -v
```

Practice on synthetic scenes (this writes nothing):

```powershell
python -B tools\den-track\den_track.py practice
```

| Column | Means |
|---|---|
| `switches` | a track moved from one person to another, silent or flagged |
| `silent` | it moved while the record said `confident`: the gate wants 0 |
| `IDF1` | pooled over the scene's crossings: the gate wants at least 0.9 |
| `contact` | share of tracked observations reported ambiguous inside the crossing's mark |
| `outside` | the same share outside every crossing: high means it doubts everything |
| `tracks`, `face` | tracks per run; the stand-in for how often a face is visible |

To walk the whole path on one synthetic scene:

```powershell
$Out = "$env:TEMP\den-track"
```

```powershell
python -B tools\den-track\den_track.py synth --scene near_pass --out $Out\t1
```

```powershell
python -B tools\den-track\den_track.py track --observations $Out\t1\observations.jsonl --out $Out\t1\track-1
```

```powershell
python -B tools\den-track\den_track.py score --take $Out\t1\observations.jsonl $Out\t1\track-1\tracks.jsonl $Out\t1\truth.jsonl --out $Out\score-1
```

Delete the folder afterwards:

```powershell
Remove-Item -Recurse -Force $Out
```

To list the open slots:

```powershell
python -B tools\den-track\den_track.py slots
```

How the command line behaves:

- **Exit codes.** 0 written or passed, 1 the gate failed, 2 refused (nothing written).
- **Output.** The tool writes its own files, as UTF-8 with LF, so no `>` is needed;
  PowerShell 5.1's `>` would write UTF-16, which the tool refuses to read. Files with
  CRLF or a BOM read the same.
- **Folders.** `--out` is refused inside a git checkout or OneDrive, and nothing is
  overwritten: a new run needs a new folder. `score` also adds to `truth.score-runs.jsonl`
  beside each truth file: hashes only, the record of what has been scored. Keep it with
  the take.

## Records

- **`track/v1`.** The field names are exactly those of `schemas.json` Track, plus
  `version`. One line per live track per frame, and one last line with `state` `ended`.
  The tracker's confidence is `association.status` and `association.hypotheses`.
  METHOD.md says what each field holds and which stay empty.
- **Inputs.** `observation` lines with the schema's fields. Other keys are ignored,
  so a `version` or rung 2's `freshness` makes no difference.
- **`den-track-truth/1`.** See `truth.py`. One line per frame gives each observation's
  true person (one capital letter, or null for "not a person"), and one line per
  crossing gives the owner's mark.
- **`crossings.jsonl`.** `score` also writes the crossings as rung 5's gate reads them,
  `{crossing_id, track_ids, interval}`, for its "`association_safe` false through
  every annotated crossing" check. A one-person occlusion or a door swap is not included.

## The staged crossings session, later

**Needs the owner's go-ahead and a second person.** It records real people, and nothing
here starts it. E2's cost is about an evening of staged walks plus labelling. The
gate: 0 silent swaps and IDF1 at least 0.9 on 20-30 crossings. Results report a single
labeller and the sample's uncertainty: 0 swaps in 20 still allows a 13.9% failure rate
at a one-sided 95% bound, and 9.5% at 30.

**1. Before any recording.** Close the slots (below), except `freeze.frozen_on`. The
detector needs to run at about 4 frames a second or better (METHOD.md, note R6). Rung 1
must have written a camera entry, or rung 2 refuses, and rung 4's labeller must exist.

**2. A pilot, if you want one.** Record a take or two, and compare the slots' proposed
values with what you see: how long someone stays hidden, and how much of the time the
tracker is in doubt. Pilot takes are never scored, and the tool holds to that: once a
take has gone through `score`, even without `--scored`, a scored run under other numbers
or code refuses it. Then close `freeze.frozen_on`, which freezes the method.

**3. Record.** R takes with this script. Leave at least 6 s between crossings, so their
padded windows do not overlap. Any number of crossings per take works; tracks restart
with each take.

| Crossings | `type` | What A and B do |
|---|---|---|
| 8 | `crossing` | Walk past each other at a normal pace, about a shoulder apart: half across the camera's view, half along it |
| 4 | `crossing` | The same, brushing shoulders |
| 5 | `crossing_occluded` | Cross behind the armchair, so both go out of sight |
| 3 | `occlusion` | One walks behind the armchair and out; the other stays in view |
| 3 | `near_pass` | Walk toward each other, stop at arm's length, turn back |
| 2 | `door` | One leaves by the door; the other comes in moments later |

```powershell
python -B tools\den-take\take.py --script free --out C:\den-takes\crossings-1\take-01
```

**4. Observations.** Run the detectors and rung 2 on each take (rung 2's README, with
`--detections`). The detectors need the owner's go-ahead too.

**5. Annotate, before any tracks exist.** You label alone and blind to system output,
with a delayed re-mark of a sample (`owner-choices.md` item 2). With rung 4's labeller,
mark each observation's person and each crossing's `interval`: from when the two come
within arm's reach, or one goes out of sight, until they are apart again or back in
view. It writes `truth.jsonl` in the take's folder.

**6. Track and score.** Once per take:

```powershell
python -B tools\den-track\den_track.py track --observations C:\den-takes\crossings-1\take-01\observe-1\observations.jsonl --out C:\den-takes\crossings-1\take-01\track-1
```

Then once for the session, with one `--take` per take. The line is long; it is one command:

```powershell
python -B tools\den-track\den_track.py score --scored --out C:\den-takes\crossings-1\score-1 --take C:\den-takes\crossings-1\take-01\observe-1\observations.jsonl C:\den-takes\crossings-1\take-01\track-1\tracks.jsonl C:\den-takes\crossings-1\take-01\truth.jsonl --take C:\den-takes\crossings-1\take-02\observe-1\observations.jsonl C:\den-takes\crossings-1\take-02\track-1\tracks.jsonl C:\den-takes\crossings-1\take-02\truth.jsonl
```

**7. Read it.** `score.json` has each crossing, the pooled numbers, the bound, the gate
and the method's fingerprint. A crossing whose window does not hold scored observations
of two people on both sides of the mark (the detector stalled, a time was mistyped, the
take was cut short) is listed as excluded and does not count; a scored run refuses when
too few are left. An agent may read `score.json`, `track-run.json` and
`truth.score-runs.jsonl`, which hold counts, ids and hashes, but not `observations.jsonl`,
`tracks.jsonl`, `truth.jsonl` or any frame. If geometry alone misses the gate, the ladder's
next step is session appearance as its own rung, after the owner's decision on learning
permissions. A take that has been scored is used. A re-score after any change to the numbers,
the code or the labels needs fresh takes, and a scored run refuses the old ones. Replaying
with nothing changed gives the same report.

## Open slots

Each has a proposed value that practice runs use. A scored run refuses while any is
open. To close one, replace its `{"open": true, ...}` object in METHOD.md's block with
the value.

| Slot | Proposed | What decides it |
|---|---|---|
| `freeze.frozen_on` | the date of the freeze | Closing it freezes the method |
| `room.floor_m` | `[0, 0, 4.572, 3.759]` | Note R10's tape measures; check against rung 1's frame |
| `room.exits_m` | `[]`, known to be wrong | The door's position in the room frame |
| `tracker.max_coast_s` | 6.0 s | How long someone can stay hidden and come back as themselves |
| `tracker.crossing_radius_m` | 1.2 m | How close counts as a crossing; the owner's mark is about 1 m |
| `tracker.reconfirm_s` | 1.5 s | How long after doubt a track is confident again; while it is not, no narrow beam and no teaching |
| `scoring.window_pad_s` | 2.0 s | How much of each crossing's before and after is scored |
| `scoring.face_visible_score_min` | 0.4 | The keypoint score that counts as a visible face (a stand-in) |
| `scoring.crossings_plan` | the script above: 12 `crossing`, 5 `crossing_occluded`, 3 `occlusion`, 3 `near_pass`, 2 `door` | How many of each kind must count. A scored run refuses when any kind falls short |

## Owner questions

1. **Back to `confident` after a crossing.** A track goes back to `confident` once doubt
   has been quiet for `reconfirm_s`. That means "one person since `since_ts`", not "the
   same person as before". Rung 5's fixture assumes it, and staying ambiguous for life
   would leave both people unlocated for narrow beams after any close pass. Is this
   the reading you want?
2. **Track has no time of its own** (rung 5 asks too). A coasting or ended line lists no
   observation, so rung 5 dates it at the line before. Rung 3's scorer needs no time on
   the line, so none was added. Should Track gain an `as_of`?
3. **What the gate counts.** The contracts do not spell out three things, which are read
   here as: IDF1 pooled over padded crossings, with whole-take IDF1 reported beside it; a
   silent swap outside any crossing also fails; flagged switches and ambiguous share are
   reported with no limit. Agreed?
4. **Frame rate.** The method is tuned at 4 frames a second and weak at 2 (METHOD.md). The
   stopped den loop placed people about once a second; R6's path sustained 4.6-4.9 Hz. The
   session needs a detector path at the higher rate. Is that the plan?
5. **Crossing annotations** (rung 5 asks too). This tool reads `den-track-truth/1` and
   writes rung 5's `{crossing_id, track_ids, interval}` from it. Should rung 4's gold set
   adopt `den-track-truth/1`?

## What is deliberately not here

- E2's body-appearance and face arms, which wait for the owner's decision on learning
  permissions. No appearance or face data is read or written.
- More than one camera. Frames from two sensors would run one after the other, and the
  second would see the same person as a newcomer. There is one camera today (note R2).
- The live path. Nothing feeds this tracker from a running detector, and nothing it
  writes reaches a light.
