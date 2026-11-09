# den-sim: the den lighting pipeline on a rendered take

Press a scene and watch the pinned den pipeline tools run on a recorded, rendered take, the
older lighting brain decide, and a virtual spotlight in the real Home swing to the couch. Then
read the exact fixture command it would take, which is shown and never sent.

**Status: a simulation, step 3 of the connected plan.** The picture is a rendered, artificial
person in an authored room, seen by a camera that does not exist. A real detector, YOLO26s and
RTMPose-m on this PC's CPU, looked at every usable frame when the set was prepared, and its rows
are recorded beside each take: pixels decide whether anyone is there. No model runs on a click,
and Qwen's answers are still typed from the take's truth, which the rows say. Nothing reaches a
bulb, the camera, the Jetson, the inference host or Home Assistant. The red strip on the page
says so, and the top line reads "Connected start to finish: not yet" with every reason.

It needs Node, Python with numpy and Pillow, and git, and no npm install to run. It has been
tested on Node 24 and Python 3.12, and `python` must be on your PATH: the harness stops and says
so if it isn't. Preparing a set also needs the detector's venv (see `tools\den-detect\README.md`).

## Run it (PowerShell 5.1)

From the folder that holds `tools\`:

```powershell
node tools\den-sim\run.mjs
```

Open http://127.0.0.1:18180/ and wait about 20 seconds for the five scene buttons. Press Ctrl+C
in the terminal to stop.

It uses the newest prepared set under your data root (below). With no household data at all,
using an authored room and sockets:

```powershell
node tools\den-sim\run.mjs --room authored
```

To use a prepared set somewhere else, add `--prepared <the set's folder>`.

## What a click does

Each button is one prepared take: reading on the couch, holding a phone, standing by the couch
with a book, nobody there, and a blank frame. The click is told the take's folder id, never the
scene. A click on **Reading on the couch** does this in about 6 seconds:

1. Every frame of the take is hashed against its `frames.csv` and the receipt prepare wrote, and
   its `take.json` is held to prepare's digest too. A frame or `take.json` that changed stops the
   click at hop 3. Its check must be the one prepare recorded, alone: `replay.py` reads the newest
   `check-N.json` for its clock, so a second one beside it (another run of `check.py` writes one)
   stops the click at hop 4.
2. The detector rows prepare recorded beside the take are held to hop 6's checks: their digest and
   `detector-run.json`'s are the ones prepare recorded, they name the pinned weights, they cover
   every usable frame, every row names YOLO26s or RTMPose-m, no frame took over 1.0 s, and prepare
   ran the model again on three frames and got the same person boxes within 1 px. Anything less
   reads MISSING at hop 6 and stops the click, never "nobody". Rows with no person stop it with
   "the detector saw nobody, nothing was asked".
3. The pinned tools run on those rows: observations, tracks and the router. Qwen's answers are
   typed from the take's truth, which hop 12 finds by the take's folder id.
4. The older lighting brain judges what they produced, with no camera position, so the reader's
   facing comes from the shoulder labels of the newest bundle entry that has both. It waits for
   3 seconds of reading.
5. One fixture swings to the reader's hand point at about 30 degrees a second, and the exact
   command appears, marked `NOT SENT`.

The panel shows the frame read back out of the take's own picture plane, with the model's person
box, its score and the supported joints from that frame's recorded row drawn on it, and a marker in
the room shows where the invented camera sits. Under the picture, one line says how far the median
height of the supported shoulders and hips over the take's last 2 s sits from the "on the couch"
limit step 5 will test, the seat's top plus 0.45 m: for example "torso 0.09 m under the limit". It
is said against the limit only, never as a height. The same line goes to the console and to the
run's `run.json`.

**Holding a phone** ends with the brain refusing and the spot off. **Nobody there** and **Blank
frame** stop at the detector: "the detector saw nobody, nothing was asked", with no box, no person
marker on the floor and no beam. **Standing by the couch with a book** stops there too: on these
renders YOLO26s gives the standing figure, close to the camera with its legs out of frame, person
scores of 0.02 to 0.09, under its 0.25 cut. That is what the model saw, and the page says so. If a
standing figure ever lights a spot, it's the older brain's own 1.5 m rule calling it on the couch,
and the panel says that on every lit result.

A second click on the same button swings 0 degrees, because the beam is already there. At this
step its top line still reads "not yet", because rows are MISSING (see below). **Reset** reloads
the page and clears the aim.

## The top line and the hop list

`hops.json` holds the 26 hops in one table, each with its finished word, its tool and pin, the
recorded inputs it may read and its named stand-ins. Each click writes a `run-receipt.json`: per
hop its word, whether it was recorded, built at page start or ran now, its tools against their
pins, and the files it read and wrote with their digests. The recorded hops take theirs from the
`prepare-receipt.json` beside each take.

`top_line.mjs` reads "yes" only when every hop has a row, every file a hop read is vouched for by
the receipt that wrote it, every row reads its finished word with only named stand-ins, every
tool matches its pin, the aim check is within tolerance and no row is MISSING. At this step it
reads "not yet". A click the pipeline stops (nobody, a refusal) reads "This click stopped at"
only once the rows up to there hold. A click that breaks reads MISSING at the hop that broke, with
why; the rows before it stay as they ran, and only the rows after it read "not reached". A second
press reads "Pressed again" only when nothing but its aim check could fail.

The list sits under four headings: "Recorded once", "Built when this page started", "Ran on this
click", and "Not built yet, or not reached on this click". Among the reasons at this step are the
truth's reads: hop 12 lists every scene's truth record it opened to find its own take's, and types
its answers from the truth's held object.

| Hop | At this step | What runs |
|---|---|---|
| 1, 2 Input image, virtual camera | Scripted on purpose | A rendered figure in an authored room, served by a camera that does not exist. Recorded once |
| 3, 4 Frames, check | Adapter | The real `take.py` and `check.py`, cues silenced, a fast clock, the harness answering the check's prompt. Recorded once, re-checked by hash on each click |
| 5 Camera registration | Adapter | The real fit, slots and labels set in memory, re-fit on your mark at page start |
| 6 Detector and box | Adapter | `tools/den-detect/detect.py` over the Lab's real YOLO26s and RTMPose-m workers, on this PC's CPU, `authorize` answered yes. Recorded once per take, `available_ts` counting compute only, checked against the model at prepare and held to its checks on each click |
| 7, 8 Observations, tracks | Real, unscored | `replay.py --detections` and `den_track.py` on the recorded rows, on open slots' proposed values. Zero tracks stops before the router, and says what was seen |
| 9 Bundles and router | Adapter | `den_bundle.py route` with its fake sender, on a method copy that adds this camera's frame size |
| 10 Occupancy | Scripted | The track list decides whether the older brain runs at all; that brain's own occupancy stays typed until step 6 |
| 11 Bundle to Qwen | Missing | Not built: no model is asked |
| 12 Qwen reply | Scripted | Answers typed from the take's truth through the real `fact_line`: the button you pressed typed them |
| 13 Need | Adapter | need v0 in den-core's runner |
| 14 Couch | Scripted, or Adapter with your mark | A declared point, or your sofa mark |
| 15, 16 Person state, light model | Missing | Not built yet |
| 17 Spot decision and aim | Adapter | The older lighting brain, with jev and deep_stack health asserted |
| 18, 19 Feed into Home, motion | Adapter, scripted | This harness replays the brain's timeline re-timed to now, with 10 Hz steps for the swing |
| 20 Home client, follower, overlay | Real | Unchanged `app/src`, served from this harness |
| 21, 22 Inverse kinematics, packets | Adapter | `compile_target` on a fictional profile, and the codec with its public test key |
| 23 Zigbee envelope, CAN frames | Scripted | Display only |
| 24 to 26 Aim check, meter, verify | Missing | Not built yet |

## Your own sockets and couch (optional)

Without these the page says `AUTHORED SOCKETS` and `DECLARED`. They go in the private folder
under your data root, stay outside git, and take effect when you restart.

For your sockets, log in to Home, paste this after Home's address with your site's id in place
of `<site>`, and save the JSON to Downloads as `registration-revision-3.json`:

```text
/api/apartment/v1/sites/<site>/maps/<site>-den/rooms/den/registration/revisions/3
```

Then copy it and your sofa mark file from Downloads:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\den-sim\private" | Out-Null
Copy-Item "$env:USERPROFILE\Downloads\registration-revision-3.json" "$env:USERPROFILE\den-sim\private\"
Copy-Item "$env:USERPROFILE\Downloads\*-den-sofa-target-v4.json" "$env:USERPROFILE\den-sim\private\"
```

The harness finds the mark by what it holds, not by its name, and uses it only with the export
it names, checked by its seal. Once both are there, the start places the picture's couch on your
mark, the reader facing away from the mark's longest edge toward the middle of the floor, and
prints one line in words: which fixtures pass for that facing and the spot, for example
`F1 clear, F2 refused: in front of reader`. If none passes, say which way to turn the reader,
and an agent writes `facing.json` there: one number of degrees, left positive. The re-fit runs
only when the mark, the export, `facing.json`, or the set's clicks or calibration changed.

Without a mark, a square stand-in seat around the declared point places the picture's couch. A
square has no single longest edge, so wherever the point is, the reader's facing is a tie-break,
not the stated rule, and the page's registration line says so.

Without a mark you can give a declared point as `couch-declared.json`, holding
`{"point_m": [x, y, z]}`.

## Where things go

The data root is `%USERPROFILE%\den-sim`, or the folder `DEN_SIM_HOME` names. Not
`%LOCALAPPDATA%`: the Claude app's processes see that folder through a private store, so a set an
agent prepared there would be invisible to your own PowerShell.

| Folder | Holds |
|---|---|
| `prepared\<set>` | One prepared set: the renders, the marks take and its fit, five scene takes with their `prepare-receipt.json`, `detections.jsonl` and `detector-run.json`, the truth and the display file. About 750 MB |
| `prepared\tries.jsonl` | Every try at choosing the picture, append-only. The page counts the Qwen tries in it |
| `private` | Your optional files, the brain geometry built from them, and `fits\`, your re-fits |
| `fits` | Re-fits on the authored room's stand-in seat |
| `runs\<stamp>\click-<n>` | Each click's rows, observations, tracks, bundles, den-core log, brain timeline, dry run, picture and `run-receipt.json`, about 1.2 MB. Nothing cleans these up |
| `tools\<name>-<sha>` | `git archive` of each pinned ref in `refs.json`, about 1 MB. `observe-<sha>` also holds the Lab files the detector imports, so it can be `detect.py`'s `--tools-root` |

To clear the runs:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\den-sim\runs"
```

A set is prepared once, by an agent, in about 3 minutes. It reads nothing in your private folder.
Hop 6 runs `tools\den-detect\detect.py` in the detector's venv on each scene take, then again on
three frames of each to check the model reproduces its rows. A missing venv or weights, a digest or
a mismatch stops prepare at hop 6: nothing fake stands in. The venv is
`%USERPROFILE%\ml\den-detector`, or the Python `DEN_DETECT_PYTHON` names. Ultralytics' settings and
caches, and the detector's TEMP, go to a work folder under the data root that prepare deletes.
If prepare stops, the set still opens: the take it stopped on reads "Prepare stopped at hop N",
and the default start names a newer refused set it passes over.
It renders with Playwright's headless Chromium, so `NODE_PATH` must name a `node_modules` holding
`playwright`:

```powershell
python -B -X utf8 tools\den-sim\scene\prepare.py
```

## What keeps it off the network

- The harness binds `127.0.0.1` only and refuses any other `Host`.
- Anything it does not serve returns 503 and is logged.
- It refuses non-GET except a same-origin `/__sim/feed`, and destroys WebSocket upgrades.
- The page's security policy allows connections to its own origin only, plus `data:` and `blob:`,
  which Home's Photo view needs for local bytes.
- Every Python process here refuses all sockets, through an audit hook. Only `prepare.py`'s
  virtual camera may use loopback, while a set is prepared. `detect.py` also sets Ultralytics
  offline with no auto-install and turns onnxruntime's telemetry off, because native code can't be
  hooked.
- There is no send control, token or route to Home Assistant anywhere.

## The tests

They stay on this PC: they need a prepared set, the pinned refs and the codec on disk, so no CI
job names them. Each points `DEN_SIM_HOME` and `LOCALAPPDATA` at scratch folders, so nothing
reads your private folder or runs, and `DEN_SIM_SET` at a prepared set. Port 18180 must be free.

```powershell
$env:DEN_SIM_HOME = "C:\tmp\den-sim-test"; $env:LOCALAPPDATA = "C:\tmp\den-sim-test"
$env:DEN_SIM_SET = "$env:USERPROFILE\den-sim\prepared\<set>"
python -B -X utf8 tools\den-sim\scene\test_scene.py
python -B -X utf8 tools\den-sim\test_chain.py
python -B -X utf8 tools\den-detect\test_detect.py
node --test tools\den-sim\scene\test-scene-figure.mjs
node --test tools\den-sim\test-den-sim.mjs
```

`test_detect.py` runs the model in the detector's venv and skips those parts without it. With
`$env:DEN_SIM_PREPARE_TRACE = "1"`, `test_scene.py` also prepares a whole set in a scratch data root
under an audit-hook trace, about 3 minutes, and checks no Python process wrote outside it.

The browser test also needs Playwright. Point `NODE_PATH` at a `node_modules` that holds it:

```powershell
node --test tools\den-sim\test-den-sim-browser.mjs
```

## What this does not show

- That anything understands a reader. A real detector finds the rendered figure on the couch,
  but Qwen's answers are typed from the take's truth until step 4.
- That the real den camera could see a reader on the couch. This camera doesn't exist.
- That the command would work. The codec on disk is older than the release `origin/main` pins,
  clock exchange is not generated, and no real radio has run this format.
- That the numbers are tuned. Every tool runs unscored, on proposed values.

## Files

| File | What it is |
|---|---|
| `run.mjs` | The loopback harness that serves the unchanged Home, the prepared set and the re-fit |
| `data-root.mjs` | Where the data root is, and the newest prepared set in it |
| `hops.json` | The 26 hops: finished words, tools, pins, recorded inputs and stand-ins |
| `receipt.mjs` | Builds each click's `run-receipt.json` |
| `top_line.mjs` | The top line: a pure function of the table and a receipt |
| `brain.mjs` | Builds the geometry and runs one click from end to end |
| `chain.py` | The frame hash, hop 6's checks on the recorded rows, the pinned tools in their own guarded processes, and the adapter |
| `brain_run.py` | The real activity loop, publisher and older brain under a fake clock, and the startup line |
| `feed.mjs` | Replays the brain's timeline into Home and inserts the motion |
| `dry_run.py` | The fixture command for the brain's target |
| `panel.js` | The strip and panel on the page |
| `refs.json` | The pinned refs, and the codec's path and digests |
| `scene\` | The recorded side: the scenes, renderer, virtual camera, frame hash and `prepare.py` |
| `..\den-detect\` | `detect.py`, hop 6's detector over the Lab's workers, and the venv's `requirements.lock` |
| `test-den-sim.mjs`, `test-den-sim-browser.mjs`, `test_chain.py`, `scene\test_scene.py`, `scene\test-scene-figure.mjs`, `..\den-detect\test_detect.py` | The tests |
