# den-track: tracks from geometry (rung 3) and E2's scorer

Rung 3 of the live den architecture (`docs/architecture/live-v1/`). It links
rung 2's `observation` records into per-person `track` records from
room-frame geometry alone, replacing the per-frame `slot-N` ids that link
nobody across frames (`notes.json` R7). It also scores E2's geometry-only
arm: silent swaps, IDF1 and the one-sided bound, per staged crossing.

Built, not run. Nothing here has touched a camera, the Jetson, the LA host,
Home Assistant or a light, and no real crossing has been recorded or
scored. Recording a session needs the owner's go-ahead and a second person.
E2's body-appearance and face arms are out of scope until the owner decides
`{{dec:learning-permissions}}`; this arm never reads `face_match` or
`appearance_vec` observations.

Python 3.8 or newer, standard library only. Every command below works in
Windows PowerShell 5.1; run them from the repository root.

## What is here

| File | What it does |
|---|---|
| `den_track.py` | The command line: `method`, `hash`, `practice`, `synth`, `track`, `score`. |
| `records.py` | Reads `observation` records, writes `track` records. Field names are exactly `schemas.json`'s. |
| `tracker_core.py`, `kalman.py`, `associate.py` | The tracker: a floor-plane Kalman filter (walking or stopped), joint association, and ambiguity intervals. |
| `truth.py` | The ground-truth format, `den-track-truth/v1`. |
| `scorer_core.py`, `scorer_math.py` | E2's numbers: IDF1, switches, silent swaps, the Clopper-Pearson bound. |
| `pipeline.py` | Take and session folders: track, then score. |
| `synth.py` | Synthetic two-person den scenes, as live-v1 records, for tests and tuning. |
| `method.py`, `METHOD.md` | The frozen numbers, their open slots and the tuning record. |
| `test_*.py` | The tests. |

## Try it

```powershell
python tools\den-track\den_track.py method
```

```powershell
python tools\den-track\den_track.py practice --seeds 5
```

```powershell
python -m unittest discover -s tools\den-track
```

`method` prints the method version, its fingerprint and every open slot.
`practice` runs E2 on synthetic scenes in memory and says it is not a
score. If `python` is not found, use `py -3` in its place.

## Records

**In:** `observation` records as JSON Lines. Used: valid `person_box` and
`keypoints` observations with a `room_position` and its covariance. A box
and keypoints of one person in one frame merge into one measurement. Body
heading comes from the shoulders and hips in `joints_room`. Records carry
`schema_version` `observation/v1`, following PersonState's
`schema_version` pattern; a record without one is read and counted as
unversioned.

**Out:** one `track` record per live track per frame, and one `ended`
record when a track ends, with `schema_version` `track/v1` and:

- `state`: `tentative`, `confirmed`, `coasting` (unseen for
  `coast_after_s`) or `ended`;
- `position` {x, y, cov}, `velocity` {vx, vy}, from the Kalman estimate;
- `heading` {deg, sd_deg} and `heading_as_of`: these move only on accepted
  shoulder and hip evidence, and not while the track is ambiguous;
- `association` {status, hypotheses, since_ts}: `confident` with `[]`, or
  `ambiguous` with named hypotheses
  `{"hypothesis": "same" | "swapped" | "newcomer", "track_id", "p"}`
  that sum to 1;
- `identity`: always unbound (`binding` "unbound", every other value
  null). Geometry binds nobody;
- `last_obs_ids`: every observation associated in that update, the box and
  the keypoints both; `[]` when none was;
- `head_pose`, `posture` and `linked_objects` are null, because this rung
  does not compute them. `appearance` is never written.

The record's time goes in a field the contracts do not name. It is an open
slot, proposed as `capture_ts`.

**Ambiguity.** When two people may have swapped, both tracks report
`ambiguous` from that frame on, with the other track named. The interval
closes once the track has been in view with no doubt for `settle_s`; each
crossing is judged on its own. A swap made inside an interval is reported,
not silent. See `METHOD.md` for how the doubt is weighed.

## Ground truth: `den-track-truth/v1`

One `truth.jsonl` per take: a `take` line (labels used, `A` and `B`, never
names; the labeller; whether they were blind; `observations_sha256`), one
`frame` line per frame giving each observation's true person (`"A"`, `null`
for no person, `"?"` for cannot tell), and one `window` line per crossing
or occlusion the owner marks. `truth.py`'s docstring has the exact shape.
Rung 4's two-person labeller is meant to write it; `synth.py` writes it for
synthetic takes.

## How a staged crossings session will run (later, with the owner's go-ahead)

Nothing below has happened. It needs rung 2 to produce observations, the
owner's go-ahead to record, and a second person.

**1. Before recording.** The owner closes every open slot in `METHOD.md`
(`method` lists them): the record's time field, the den's doorway and
floor extent, `confident_at`, `coast_after_s`, and the crossings plan. Then
the method is frozen: `method` prints the fingerprint that every take will
be tracked and scored under. The owner's answer on recording
(`owner-choices.md`, recording-retention) applies: takes stay on household
computers, never in git or the cloud.

**2. The staged script.** R takes, one staged event each, R being the
crossings plan's total (proposed 30): 12 crossings (8 at an arm's length or
more, 4 brushing shoulders, some across the camera's view and some along
it), 6 crossings behind the armchair, 4 times one person walks behind the
armchair and out while the other stays put, 4 near passes (walk up to each
other, stop, turn back), and 4 at the door (one leaves, the other comes in
moments later). Before each take, A and B agree who starts where. A
spoken or written take number keeps the takes apart. Recording runs
through rung R's capture path, not this tool.

**3. One folder per take,** outside the repository, for example:

```text
$env:USERPROFILE\den-e2\2026-11-20\take-01\observations.jsonl
```

**4. The owner's annotation, blind.** Before looking at any tracker output,
the owner labels every person observation of each take A, B, none or
cannot tell, and marks each crossing or occlusion as a window. That is
`truth.jsonl` in the same folder. Its `observations_sha256` is what this
prints:

```powershell
python tools\den-track\den_track.py hash --file "$env:USERPROFILE\den-e2\2026-11-20\take-01\observations.jsonl"
```

Per the owner's labelling choice, the owner labels alone, re-marks a
sample later, and the result reports a single labeller.

**5. Track, then score:**

```powershell
python tools\den-track\den_track.py track --session "$env:USERPROFILE\den-e2\2026-11-20"
```

```powershell
python tools\den-track\den_track.py score --session "$env:USERPROFILE\den-e2\2026-11-20"
```

`score` refuses if a slot is still open, if any take was tracked under
another method, if observations changed after tracking, or if a truth file
does not name its observations. It prints the gate and writes the full
report to `e2-report.json` in the session folder.

**6. The gate.** 0 silent swaps (no identity switch while the track said
`confident`) in every window and outside them, pooled IDF1 at least 0.9,
and at least 20 windows. The report also gives the one-sided 95% upper
bound on the silent-swap rate, which with 0 failures is
1 - 0.05^(1/n): about 13.9% at 20 crossings and 9.5% at 30. It also gives
the reported switches and the share of ambiguous records, so a tracker that
is always ambiguous cannot pass unseen.

**7. If the gate is missed.** Per the ladder, session appearance becomes
its own rung after the owner's decision on learning permissions. A take
looked at for diagnosis moves to development. A retune is a new method
version, and confirming it needs fresh takes.

## Tests

`python -m unittest discover -s tools\den-track` runs them all: clean
crossings keep ids; a touching crossing lowers confidence instead of
swapping silently, and names the other track; a person comes back from
behind the armchair on the same track; people enter and leave by the door
and a newcomer gets a new id; output is deterministic and causal; record
fields match `schemas.json`; CRLF and LF read the same; IDF1 and the bound
on known cases; the scored-run refusals; and no connection to anything but
127.0.0.1. Tests use synthetic data in temporary folders only.
