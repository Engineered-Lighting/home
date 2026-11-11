# den-sim: the den lighting pipeline on a rendered take

Press a scene and watch the pinned den pipeline tools run on a recorded, rendered take. Once your
one paste has recorded Qwen's words on that take's exact frames, they decide whether the person is
reading, rung 9's coordinator decides the beam, and a virtual spotlight in the real Home swings to the
couch; then read the exact fixture command that Decision would take, which is shown and never sent. Until
that paste, no Qwen reply is on file: a click with a person stops at the reply, the beam stays off,
and the page shows the line to paste.

**Status: a simulation, step 6 of the connected plan.** The picture is a rendered, artificial
person in an authored room, seen by a camera that does not exist. A real detector, YOLO26s and
RTMPose-m on this PC's CPU, looked at every usable frame when the set was prepared, and its rows
are recorded beside each take: pixels decide whether anyone is there. Qwen on the inference host
looks at the stills each request would send, once, when you paste one line (see "Qwen's words",
below), and its replies are recorded on this PC: a click rebuilds each request's exact bytes and
looks the reply up by their hash. No model runs on a click, and nothing on a click reaches a bulb,
the camera, the Jetson, the inference host or Home Assistant. The red strip on the page says so,
and the top line reads "Connected start to finish: not yet" with every reason.

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
scene. A click on **Reading on the couch** does this in about 15 seconds on this PC (about 10 s
to the click's receipt, then the swing; 13.5 s measured in a headless browser):

1. Every frame of the take is hashed against its `frames.csv` and the receipt prepare wrote, and
   its `take.json` is held to prepare's digest too. A frame or `take.json` that changed stops the
   click at hop 3. Its check must be the one prepare recorded, alone: `replay.py` reads the newest
   `check-N.json` for its clock, so a second one beside it (another run of `check.py` writes one)
   stops the click at hop 4.
2. The detector rows prepare recorded beside the take are held to hop 6's checks: their digest and
   `detector-run.json`'s are the ones prepare recorded, they were recorded for this take and its
   check, they name the pinned weights, they cover every usable frame, every row names YOLO26s or
   RTMPose-m, no frame took over 1.0 s, and prepare ran the model again on three frames and got
   the same people: boxes and joints within 1 px, scores within 0.001. Anything less reads MISSING
   at hop 6 and stops the click, never "nobody". Rows with no person stop it with "the detector saw
   nobody, nothing was asked".
3. The pinned tools run on those rows: observations, tracks and the router. Hop 10 writes one
   occupancy event, on via `den-track`, at the first confirmed Track line, dated by the observations
   it names: what the coordinator reads as someone in the room. No track confirmed, no event.
4. For each router request, hop 11 takes its bundle, chooses 2 or 3 stills with the Lab's own
   `qwen_live.spread`, finds each still's frame by its time (within 1 ms in `frames.csv`), encodes
   it as a JPEG with Pillow at fixed settings, and builds the exact body the Lab's
   `qwen_scene.request_body` would send, with the time label fixed at "evening 20:00". Hop 12 looks
   the reply up by that body's sha256 in the reply store and turns it into a fact line with the
   real Lab builders. A request whose bundle is missing or too thin for two stills is not asked,
   and the walk goes on. With no reply on file for a body, the click stops there: "Qwen has not
   seen these exact frames", need gets no input and the beam stays off.
5. `person.py` (hop 15) writes `person_states.jsonl` in the field names of the
   architecture's `person_state` record: one unchanged need v0 per track (hop 13, the only need pass
   on a click), fed only the replies whose bundle was safe to tie to that track, and rules on the
   track's own joints, each using only what was known when the reply arrived. Besides the bundles,
   replies and room model, it reads hop 7's observations, only to date each Track line, which carries
   no time of its own. It tests whether the person is on the couch: their position within 0.15 m of
   the couch's footprint, and the median height of their supported shoulders and hips over 2 s at most
   the seat's top plus 0.45 m, or with no hip, shoulders at most 1.20 m above the floor, both compared
   in whole millimetres. Seated, the spot is `sofa-book`; otherwise `handheld`, which has no target.
   Each field's support time is the capture time of the newest evidence it rests on, and a state is
   written when a value or its support status changes. On the real takes, whose first bundle isn't
   safe, need engages on the third reply.
6. **Rung 9's coordinator decides the beam** (hop 17), in a spot mode written for this simulation, at
   den-core's pin: once, on the occupancy event and the person states, through den-core's real runner
   with the plant off, on hop 16's light model. Its readers are the tracks `decide.reading` calls
   reading; a reader at `sofa-book` gets a target of 300 in the model's unit (modeled lux on assumed
   lumens), any other spot none. Each fixture is held to the lighting brain's own `aim.py`, imported
   by den-core's `spot_rule.py` (too far, beam through the head, in front of the reader, pan or tilt
   out of reach), and the solve takes the lowest cost over every allowed fixture and level, or off:
   one beam per reader. It writes canon Decisions on `virtual-den-4`, `actuation: synthetic`, with the
   aim as `target_m` and a dark move then a fade in; `check.py` replays the log byte for byte. Nobody
   reading writes no Decision, and the click stops there; a reader every fixture refuses gets one
   Decision with no steps, the beam stays off and the refusals show.
7. Each Decision goes through `decision_doc.mjs` into Home's document (hop 18); the feed swings the
   lit fixture from Home's read-back to the Decision's `target_m` at about 30 degrees a second (hop
   19), and keeps re-timing the last Decision's document once a second. The fixture command is
   compiled from the Decision's target and level and appears, marked `NOT SENT` (hops 21 to 23). At
   landing the page reads Home's aim back and the harness holds it to the Decision (hop 24, below).

At page start, after the re-fit, `couch_to_room.mjs` (hop 14) checks the couch's seal and binding with
Home's own validator and appends one revision to a copy of the fit's private `room-model.jsonl`, by
den-register's own rule: a seat surface `sofa`, its landmark `sofa-seat`, and the spot `sofa-book`
0.30 m above the seat point. The fit's own file is left as its receipt names it. That copy is the room
model every click reads, held to the digest written at page start, and the couch is placed from the
very seat bytes the re-fit read. With your sofa mark (below) the couch is your mark, and hop 14 reads
ADAPTER. Without it, the couch is the public stand-in seat sealed around the DECLARED point, and hop
14 stays SCRIPTED. On a click that stops before hop 14, its row keeps the word it was built with,
greyed.

Then `light_model.mjs` (hop 16) builds the light model, before any picture, and the same file serves every take:
Home's own beam formula from `lit-model.mjs`, unchanged, from each of the four fixtures' aim origins (the geometry
`brain.mjs` builds, from your registration export or the authored sockets) to the `sofa-book` spot in that room model.
It is the Stage-1 shape den-core and den-verify read: channels `F1.spot` to `F4.spot`, one 2700 K basis row per
fixture at full, Home's dimming curve at the solve's own brightness values, `aim_points`, and per channel its aim
origin and limits, keyed F1 to F4, so no fixture id enters it. 450 lm is assumed, the 20 degree beam is the datasheet's,
and there is no occlusion, bounce or daylight, so its unit is `relative` (modeled lux on assumed lumens) and it is marked
`fake: true`. It holds coordinates, so it is written privately beside the fit, as `light\light-model.json`; its digest
goes into the startup record, `run.json` and the console, and hop 16's row lists the room model and geometry it read,
each against the digest its writer took. Hop 16 reads ADAPTER, "built at page start"; on authored sockets its row also
says the fixture origins are not yours. The coordinator decides from it, and hop 17's row holds it to its digest.

The empty room Home is shown before and between clicks is `decision_doc.mjs`'s off document: four dark fixtures and
the full field set of Home's contract fixture, its reading evidence unbound; it stands in for any click that writes no
Decision. `decision_doc.mjs` maps each spot Decision to Home's document; it refuses anything but `virtual-den-4` and
`synthetic`, takes each fixture's level and colour from the Decision's read-back, its point from the request and its fade
from the fade-in's two times, and writes `bound: true` only when the reader's person state joins through `asks.jsonl`
to one request, bundle, track and reply. Every document the feed sends goes through Home's own validator first: one it
refuses stops the click with the validator's reason, and the off document stays.

## The Decision card, the command and the aim check

Under the person card, the Decision card says what the coordinator chose, from hop 17's row: for example "Chose F2
(fixture-02): level 0.79, predicts 273 of 300 asked, modeled lux, assumed lumens", the fixtures it allowed that cost more,
and each refusal in words ("F1 refused: in front of reader"). When every fixture refuses, the beam stays off, the reasons
show, and the card says you can turn the reader (see `facing.json`, below). With no Decision, as on a phone click, the card
says why from the person state: nobody is reading. The fixture command box below it shows the motor units, the packets and
the Zigbee command compiled from the Decision's target and level, marked NOT SENT (`sent: false`, `send_path: null`), and
the aim check's two numbers. A target the fictional profile can't reach is refused and shown.

The aim check (hop 24) runs at landing: the page reads the lit fixture back from Home's real overlay and posts it to the
harness's same-origin `/__sim/aim`, and the harness writes `aim_check.json` beside the click with two angles: the
Decision's target against Home's target point, seen from the fixture, and the inverse kinematics against Home's pan and
tilt (fixture pan = -Home's pan, fixture tilt = 90 - Home's tilt), each within 0.05 degrees. Commanded, no encoder, not a
measurement. Out of tolerance, the row shows both numbers and the top line reads "not yet: Home did not follow the
Decision".

The last Decision stays lit after the take ends, re-timed. Once the last reply's validity has passed on the chain's clock,
the page adds "Last reply N s old. The live system would drop this beam."

The click is told the take's folder id and Home's report of the fixtures, never the scene. The
button labels live in the set's `display.json`, which only the panel reads (without it, the
buttons show folder ids and every click runs the same), and the scenes' true joints in its
`truth\` folder, which no click opens.

The panel shows the frame read back out of the take's own picture plane, with the model's person
box, its score and the supported joints from that frame's recorded row drawn on it, and a marker in
the room shows where the invented camera sits. The note under it repeats the score. When the rows
fail hop 6's checks, no box is drawn and the note says the rows did not pass, never that the frame
holds no person. Under the picture, the person card says what hop 15 found, per track: how far the
person is from the seat point across the floor, "on the couch: yes" or "no" with which half of the
test failed, the spot by name (the `sofa-book` spot's marker in the room comes at step 7) and the
track's engagement. A track whose replies were all held reads "not tested yet", and one with no
supported shoulder or hip says that half failed. It says the couch is the picture's couch, placed on
your mark or on a DECLARED stand-in seat, says the facing is yours when `facing.json` turned the
reader, and that the coordinator decides the beam from this state, with no target unless the spot is
`sofa-book`. It holds distances and words only, never a coordinate.
The same words go to the console and to the run's `run.json`.

Under it, the Qwen card lists each request: its stills, shown as the very JPEGs its body carries
(the click writes them under its run folder's `stills\`, by their sha256), their frames, how far
each is before the newest, and Qwen's words with every field, the model's served root and the day
it was asked. It says each reply is a plain file with no signature, so a hand-typed one would
pass. The red strip says whose words are on file: none yet, a fake vLLM's (a test's), or Qwen's,
recorded once by your paste. Home's document says `inference_ran` and the activity source
`den_sim_la_direct` only on a click that used Qwen's recorded words, and its inputs are marked
scripted otherwise; a click's occupancy source is `den_sim_den_track`. **Reading on the
couch** lights only if Qwen says reading there and a fixture passes; **Holding a phone** stays off unless
Qwen says reading, and then the coordinator writes no Decision because of what Qwen said. Until you paste
the line below, both stop at hop 12. **Nobody there** and **Blank
frame** stop at the detector: "the detector saw nobody, nothing was asked", with no box, no person
marker on the floor and no beam. **Standing by the couch with a book** stops there too: on these
renders YOLO26s does not find the standing figure, close to the camera with its legs out of frame.
Run as the Lab's worker runs it, its best person score on each of the take's 82 usable frames is
0.006 to 0.049 (median 0.015), far under its 0.25 cut; with Ultralytics' default rectangular
letterbox instead, 0.01 to 0.10. That is what the model saw, and the page says so, so Qwen is
never asked about it. A standing figure the detector did find would read "on the couch: no", get
`handheld`, which has no target, and no beam.

A second click on the same button swings 0 degrees, because the beam is already there. At this
step its top line still reads "not yet", because hops 25 and 26 are MISSING until step 7 (see below).
**Reset** reloads the page and clears the aim.

**Run every scene** presses all five in turn with no reload, swings the beam where each lights,
and fills a plain table: scene, box, Qwen (asked or not, and its words), person (on the couch, not on
the couch, not tested when every reply was held, MISSING with why, or none when no track reached hop
15), beam (the Decision's fixture and level), and why it stopped, from the coordinator. It runs no aim check. It checks that every reply on the table came from one model, says "no meter on this
run", and leaves the top line as your last single click set it.

## Qwen's words: the one paste

Qwen is asked once per set, by you, never by a click. An agent first runs
`python -B -X utf8 tools\den-sim\scene\prepare.py --owed`, which runs every scene take through
the same walk a click does and files each body that has no reply as
`prepared\owed\<body_sha256>.body`: hashes only, no scene name. While any is owed, the page says
"Qwen still owes N replies" and shows this line with a Copy button. It counts only an owed list
filed for the set it shows; a list for another set is named, and not counted. Paste it into a new
PowerShell window, since `run.mjs` holds its own; it works from any folder:

```powershell
python -B C:\claude\home\.claude\worktrees\den-sim-connected\tools\den-sim\ask_la.py
```

It refuses to start without a console, and then writes nothing. It lifts the sofa look's `la.py`
and `ask.py` and the Lab's `qwen_scene.py` with `git archive` at the commits `refs.json` pins under
`lifted`, each held to its blob. With nothing owed, and no folder an earlier paste left behind, it
says so and doesn't contact the inference host at all. Otherwise, two visits over ssh, as the model
test's paste did: a check visit (the served name `qwen3-vl-30b` with the root of its weights named,
the GPU under 77 C with 8 GiB free; no image is sent), and only if that passes, an ask visit in a fresh private `/dev/shm` folder,
removed afterwards: one request per owed body, 4 s apart, a 20 s timeout, one re-send on no answer,
a stop after two misses, and never more requests than the bodies plus 2. Every ssh and scp has a
local timeout, so a stalled link stops the paste at its step. The replies come back to
`prepared\replies\<body_sha256>.json`: the time it was asked, the host, the served root, the HTTP
status, vLLM's whole response and the seconds it took. Only a reply with all six fields, a root that
names a model and its seconds is filed; anything else leaves its body owed. Nothing it prints holds
a body, a reply or an error's text. It writes `prepared\last-ask.json`: where it stopped and why,
how many came back, how many are still needed, each reply's digest, which a click's receipt is held
to, and the commit and blobs of `ask_la.py` and `ask_one.py` it ran, which hop 11's row cites once
replies are used; the page shows it. It adds one line to `prepared\tries.jsonl`. If the inference
host's vLLM is down, nothing is sent and it says where its start line is documented.

Before the paste, reading and phone stop at hop 12. After it, a click reads Qwen's words, and hop
12 reads "real words, recorded once": its clocks moved onto the chain's, its source id rewritten,
and a re-ask could differ. Replies from two models read MISSING, and so does a reply file short of
the six fields. A reply whose served root is
`fake-vllm` is a test's, and reads SCRIPTED. A body the click builds that differs from the one
asked about (other stills, another Pillow, a changed prompt) is a lookup miss, and the page shows
the paste line again.

## The top line and the hop list

`hops.json` holds the 26 hops in one table, each with its finished word, its tool and pin, the
recorded inputs it may read and its named stand-ins. Each click writes a `run-receipt.json`: per
hop its word, whether it was recorded, built at page start or ran now, its tools against their
pins, and the files it read and wrote with their digests. The recorded hops take theirs from the
`prepare-receipt.json` beside each take.

`top_line.mjs` reads "yes" only when every hop has a row, every file a hop read is vouched for by
the receipt that wrote it, every row reads its finished word with only named stand-ins, every
tool matches its pin (and, where `hops.json` names pins for a hop, as hop 6's, is held to the one
named for its path), the aim check is within tolerance and no row is MISSING. At this step it
reads "not yet": hops 25 and 26, the virtual meter and verify, are step 7's. An aim check out of
tolerance reads "not yet: Home did not follow the Decision". A click the pipeline stops (nobody,
nobody reading, every fixture refusing) reads "This click stopped at" only once the rows up to there
hold, which with the DECLARED stand-in seat or authored sockets they don't. A click that breaks reads MISSING at the hop that broke, with
why; the rows before it stay as they ran, and only the rows after it read "not reached". A second
press reads "Pressed again" only when nothing but its aim check could fail.

The list sits under four headings: "Recorded once", "Built when this page started", "Ran on this
click", and "Not built yet, or not reached on this click". Hop 12 lists each reply it read, held to
the digest `last-ask.json` recorded when the paste filed it.

| Hop | At this step | What runs |
|---|---|---|
| 1, 2 Input image, virtual camera | Scripted on purpose | A rendered figure in an authored room, served by a camera that does not exist. Recorded once |
| 3, 4 Frames, check | Adapter | The real `take.py` and `check.py`, cues silenced, a fast clock, the harness answering the check's prompt. Recorded once, re-checked by hash on each click |
| 5 Camera registration | Adapter | The real fit, slots and labels set in memory, re-fit on your mark at page start |
| 6 Detector and box | Adapter | `tools/den-detect/detect.py` over the Lab's real YOLO26s and RTMPose-m workers, on this PC's CPU, `authorize` answered yes. Recorded once per take, `available_ts` counting compute only, checked against the model at prepare and held to its checks on each click |
| 7, 8 Observations, tracks | Real, unscored | `replay.py --detections` and `den_track.py` on the recorded rows, on open slots' proposed values. Zero tracks stops before the router, and says what was seen |
| 9 Bundles and router | Adapter | `den_bundle.py route` with its fake sender, whose outcome field isn't read, on a method copy that adds this camera's frame size |
| 10 Occupancy | Adapter | One occupancy event from the track list (`den-track`), at the first confirmed track: the live source, Frigate on Hailo, has no virtual stream |
| 11 Bundle to Qwen | Adapter | Each request's exact bytes, rebuilt on each click: whole frames, no box, the time label fixed, the placement text the real camera's. Asked once, by your paste, straight to vLLM |
| 12 Qwen reply | Adapter | Qwen's real words, recorded once and looked up by the body's hash, through the Lab's real builders, clocks moved and source id rewritten. Missing until your paste; a fake vLLM's reads Scripted |
| 13 Need | Adapter | need v0, standing in for need v1, once per track in `person.py`: no other need pass runs on a click |
| 14 Couch | Scripted, or Adapter with your mark | `couch_to_room.mjs` writes the DECLARED stand-in seat, or your sofa mark, into the pipeline's own RoomModel at page start, with the spot 0.30 m above the seat point |
| 15 Person state | Adapter | `person.py`: need v0 per track and rules on real joints, unscored. Rung 6 isn't built. The coordinator reads it |
| 16 Light model | Adapter | `light_model.mjs` at page start: Home's own beam formula on assumed numbers, `fake: true`, unit relative, one private file for every take |
| 17 Spot decision and aim | Adapter | Rung 9's coordinator in a spot mode written for this simulation, at den-core's pin: its own solve and target, the lighting brain's fixture rule imported; plumbing, which its own `score.py` refuses |
| 18, 19 Feed into Home, motion | Adapter, scripted on purpose | One mapping from each Decision to Home's document, re-timed to now and the last one kept lit; 10 Hz steps at 30 deg/s for the swing |
| 20 Home client, follower, overlay | Real | Unchanged `app/src`, served from this harness |
| 21, 22 Inverse kinematics, packets | Adapter | `compile_target` on a fictional profile, and the codec with its public test key, on the Decision's target and level |
| 23 Zigbee envelope, CAN frames | Scripted on purpose | Display only, from the Decision: NOT SENT |
| 24 Aim check | Adapter | At landing, Home's read-back against the Decision and the inverse kinematics, 0.05 degrees: commanded, no encoder |
| 25, 26 Virtual meter, verify | Missing | Step 7 |

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
and an agent writes `facing.json` there: one number of degrees, left positive. The start then
names the turn, and the person card says the facing is yours. The re-fit runs only when the mark,
the export, `facing.json`, or the set's clicks or calibration changed.

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
| `prepared\<set>` | One prepared set: the renders, the marks take and its fit, five scene takes with their `prepare-receipt.json`, `detections.jsonl` and `detector-run.json`, the truth (read by tests only) and the display file (read by the panel only). About 750 MB |
| `prepared\owed` | The request bodies Qwen still owes a reply, `<body_sha256>.body`, and `owed.json` listing their hashes. `prepare.py --owed` keeps it to the current set's |
| `prepared\replies` | Qwen's replies, `<body_sha256>.json`, filed by your paste. Never removed by any tool here |
| `prepared\last-ask.json` | What your last paste did, and each reply's digest |
| `prepared\tries.jsonl` | Every try at choosing the picture, append-only, and one line per paste. The page counts the Qwen tries in it |
| `private` | Your optional files, the brain geometry built from them, and `fits\`, your re-fits, each with `couch\room-model.jsonl`, the copy hop 14 wrote the couch into, and `light\light-model.json`, hop 16's light model |
| `fits` | Re-fits on the authored room's stand-in seat, with their `couch\room-model.jsonl` and `light\light-model.json` |
| `runs\<stamp>\click-<n>` | Each click's rows, observations, tracks, bundles, occupancy, person states, the coordinator's `decide\` folder (`log.jsonl`, `decisions.jsonl`, `surfaces.json`), Home's documents, dry run, `aim_check.json`, picture and `run-receipt.json`, about 1.2 MB. They hold the room's coordinates, so they are private like `private`. Nothing cleans these up |
| `tools\<name>-<sha>` | `git archive` of each pinned ref in `refs.json`, about 1 MB. `observe-<sha>` also holds the Lab files the detector imports, so it can be `detect.py`'s `--tools-root`; `core-<sha>` the Lab's `qwen_live.py` and the 7 files it imports. `tools\lifted\` holds what `ask_la.py` lifts |

To clear the runs:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\den-sim\runs"
```

A set is prepared once, by an agent, in about 3 minutes. It reads nothing in your private folder.
Hop 6 runs `tools\den-detect\detect.py` in the detector's venv on each scene take, then again on
three frames of each to check the model reproduces its rows: each person's box and joints within
1 px, its scores within 0.001. A missing venv or weights, a digest or
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
- It refuses non-GET except a same-origin `/__sim/feed` and `/__sim/aim`, and destroys WebSocket upgrades.
- The page's security policy allows connections to its own origin only, plus `data:` and `blob:`,
  which Home's Photo view needs for local bytes.
- Every Python process here refuses all sockets, through an audit hook. Only `prepare.py`'s
  virtual camera may use loopback, while a set is prepared. `detect.py` also sets Ultralytics
  offline with no auto-install and turns onnxruntime's telemetry off, because native code can't be
  hooked.
- Only your paste of `ask_la.py` reaches the inference host, over ssh, and nothing a click runs
  imports or starts it. On that host, `ask_one.py` may open loopback sockets only, to vLLM.
- There is no send control, token or route to Home Assistant anywhere.

## The tests

They stay on this PC: they need a prepared set, the pinned refs and the codec on disk, so no CI
job names them. Each points `DEN_SIM_HOME` and `LOCALAPPDATA` at scratch folders, so nothing
reads your private folder or runs, and `DEN_SIM_SET` at a prepared set. Port 18180 must be free.

```powershell
$env:DEN_SIM_HOME = "C:\tmp\den-sim-test"; $env:LOCALAPPDATA = "C:\tmp\den-sim-test"
$env:TEMP = "C:\tmp\den-sim-test\tmp"; $env:TMP = $env:TEMP
$env:DEN_SIM_SET = "$env:USERPROFILE\den-sim\prepared\<set>"
python -B -X utf8 tools\den-sim\scene\test_scene.py
python -B -X utf8 tools\den-sim\test_chain.py
python -B -X utf8 tools\den-sim\test_ask.py
python -B -X utf8 tools\den-sim\test_person.py
python -B -X utf8 tools\den-detect\test_detect.py
node --test tools\den-sim\scene\test-scene-figure.mjs
node --test tools\den-sim\test-den-sim.mjs
node --test tools\den-sim\test-den-sim-honesty.mjs
```

`test_ask.py` and `test-den-sim-honesty.mjs` never reach the inference host. Their replies come from
a fake vLLM on `127.0.0.1` that serves the root `fake-vllm`, through a local transport in place of
ssh and scp, into a scratch data root. An audit hook in each test process refuses to start any
process whose executable or command line names `ssh` or `scp`: directly, or through `shell=True`,
`cmd /c`, `powershell -Command`, `bash -c` or `python -c`. It doesn't catch a command line that
only builds the name at run time (from an environment variable, or inside `python -c`), or a child
Python running a script file of its own; no test runs either, and `ask_la.py`'s own ssh class runs
inside a test's process, under the hook.

`test_detect.py` runs the model in the detector's venv and skips those parts without it. With
`$env:DEN_SIM_PREPARE_TRACE = "1"`, `test_scene.py` also prepares a whole set in a scratch data root
under an audit-hook trace, about 3 minutes, and checks no Python process wrote outside it.

The browser test also needs Playwright. Point `NODE_PATH` at a `node_modules` that holds it:

```powershell
node --test tools\den-sim\test-den-sim-browser.mjs
```

## What this does not show

- That anything understands a reader in your den. A real detector finds the rendered figure on
  the couch, and Qwen's recorded words say what it saw in a render, once: a re-ask could differ.
  The reply files are plain files with no signature, so a hand-typed one would pass.
- That the real den camera could see a reader on the couch. This camera doesn't exist.
- That the command would work. The codec on disk is the release `origin/main` pins, with LF line
  endings where those pins hash CRLF; clock exchange is not generated, and no real radio has run this format.
- That the light is right. The coordinator decides on a light model marked `fake: true`, on assumed
  lumens, and until step 7 nothing meters or verifies the light: only the aim check judges where it points.
- That the numbers are tuned. Every tool runs unscored, on proposed values.

## Files

| File | What it is |
|---|---|
| `run.mjs` | The loopback harness that serves the unchanged Home, the prepared set and the re-fit |
| `data-root.mjs` | Where the data root is, and the newest prepared set in it |
| `hops.json` | The 26 hops: finished words, tools, pins, recorded inputs and stand-ins |
| `receipt.mjs` | Builds each click's `run-receipt.json` |
| `top_line.mjs` | The top line: a pure function of the table and a receipt |
| `brain.mjs` | Builds the fixture geometry, from brain-targets.mjs, at page start: nothing else |
| `click.mjs` | Runs one click: the chain, its Decisions into Home's documents, the dry run, the receipt, and hop 24's two numbers |
| `chain.py` | The frame hash, hop 6's checks on the recorded rows, the pinned tools in their own guarded processes, each request's exact body and its reply's lookup, the occupancy event, and the coordinator in spot mode; also the startup line, through den-core's `spot_rule.py` |
| `couch_to_room.mjs` | Hop 14: your mark, or the stand-in seat, into the click's RoomModel, with the spot |
| `light_model.mjs` | Hop 16: the light model, Home's own beam formula from each fixture to the spot, built at page start |
| `decision_doc.mjs` | Hop 18's mapping: a spot Decision to Home's document, and the off document |
| `person.py` | Hop 15: person states in the architecture's field names, need v0 per track, rules on real joints |
| `ask_la.py`, `ask_one.py` | Your one paste on this PC, and what it runs on the inference host |
| `feed.mjs` | Plays each Decision's document into Home, inserts the motion and keeps the last one lit, each document held to Home's validator |
| `dry_run.py` | The fixture command for the Decision's target and level |
| `panel.js` | The strip and panel on the page |
| `refs.json` | The pinned refs, and the codec's path and digests |
| `scene\` | The recorded side: the scenes, renderer, virtual camera, frame hash and `prepare.py` |
| `..\den-detect\` | `detect.py`, hop 6's detector over the Lab's workers, and the venv's `requirements.lock` |
| `test-den-sim.mjs`, `test-den-sim-honesty.mjs`, `test-den-sim-browser.mjs`, `test_chain.py`, `test_ask.py`, `test_person.py`, `scene\test_scene.py`, `scene\test-scene-figure.mjs`, `..\den-detect\test_detect.py` | The tests |
