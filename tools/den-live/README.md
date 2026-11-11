# den-live

Tools for the live den view: the den camera, live, to markers and a virtual reading spotlight in Home. Steps 1 to 3 are here so far: the sofa check, registering the camera to your scan, and your marker live on this PC.

## Step 1: the sofa check

Does the re-aimed den camera see a reader on the sofa? One paste and about three minutes of sitting answer it.

The check needs these, from earlier work on this PC:
- the detector's venv at `%USERPROFILE%\ml\den-detector`;
- the camera token in `C:\den-takes\private\camera-token.txt`;
- `ssh hav-ubuntu` working from your PowerShell, as the den simulation's Qwen paste needs.

### Once: the camera token

If `C:\den-takes\private\camera-token.txt` doesn't exist yet, follow `tools/den-take/README.md`, step 2. That's on `claude/rung-r` and in the pinned den-take tree under `%USERPROFILE%\den-live\tools\observe-*`. Its three lines save the token and print only its length, which should be 24 or more. Never open the file or paste the token anywhere.

### The check

With the room lit and nobody else in the den, paste:

```powershell
python -B C:\claude\home\.claude\worktrees\den-live\tools\den-live\sofa_check.py
```

It speaks the cues:
1. A 15 s countdown. Be on the sofa with your book and your phone.
2. Read, with the book at chest height, for about 30 s.
3. Your phone instead, for about 30 s.
4. Leave the den, or step out of the camera's view, until it says "done".

Then it looks at the frames for a minute or two, and asks Qwen on LA six times. Its preflight runs first, and no image is sent unless that passes. It prints one block ending in a verdict.

| Verdict | Means |
|---|---|
| SOFA SEEN | The detector sees you reading on 80% or more of the frames, with face, shoulders and torso depth. Qwen calls 2 or more of 3 reading requests reading. The spotlight part goes ahead |
| PERSON ONLY | The camera sees you, but Qwen doesn't see you reading. By your rule the spotlight part stops, and the markers go on |
| NOT SEEN | The detector part fails |
| CHECK AGAIN | A person showed while the sofa was meant to be empty. Paste again, out of view |
| DETECTOR ONLY | Qwen couldn't be asked, for example vLLM is down. The reason is printed |

"Seated" isn't judged here. It needs the floor, which the camera registration in step 2 gives.

### What's kept

Everything stays in `%USERPROFILE%\den-live\private\sofa-check-<time>\`. That's about 250 MB of raw, unblurred frames, from the three windows only. Alongside them are the six requests and Qwen's replies, the counts and `verdict.json`.

Agents never open that folder. Nothing goes into git, OneDrive or the cloud. The stills sent to Qwen cross to LA, as the Lab's own Qwen calls do. The LA folder is removed after the ask, though vLLM's image cache may hold them for a while.

### Tests

```powershell
python -B -X utf8 tools\den-live\test_sofa_check.py
```

- No camera, LA or ssh. A fake camera serves the den simulation's rendered takes, and the ask runs against the simulation's fake vLLM.
- The end-to-end part needs the detector's venv and a prepared den-sim set, either `DEN_SIM_SET` or the default set. It takes about 2 to 3 minutes.

## Step 2: register the camera to your scan

The markers draw in your scan's frame, so the camera has to be placed in it again after the re-aim. One paste and about 20 minutes, most of it clicking:

```powershell
python -B C:\claude\home\.claude\worktrees\den-live\tools\den-live\register_now.py
```

It needs the camera token (step 1's), Node on the PATH (the den simulation needs it too), and Python with numpy and Pillow.

1. **The empty den, 20 s.** It says "leave the den", waits 15 s, records 20 s, then says you can come back.
2. **The depth check.** Since 2026-11-11 the camera service corrects its own depth (x0.889), and every frame says so. If any frame doesn't, for example after a re-flash, it stops here. den-live adds no factor of its own. `REGISTER-LIVE-20261111.md` has why and what's left over: about 2% at the floor and at the far wall, in opposite directions.
3. **The floor guard.** It finds the floor in that depth and compares the lens's height with your 1.37 m tape. If they're more than 5% apart it stops here, before you click anything, and prints both numbers.
4. **Six clicks on the camera's picture.** That's den-register's own click page. Pick features that are spread out, at different heights and depths, solid and matte: two floor-level corners, two at table height, two high up (a door frame, a shelf). Avoid glass, screens and black fabric. Prefer features in the middle of the room over ones on the far wall: the depth scale is least sure out there. Each click shows the depth it found. All six need a click with depth; if a spot has none, pick another feature for that row. Remember what each one is.
5. **The same six in your scan.** Your scan opens on a local page, with the camera's picture beside it and your clicks numbered. For each row, orbit to the feature and click it once without dragging. Then Save.
6. **The fit.** den-register fits with the 0.10 m gate and prints `REGISTERED den-oak@<date>.<n>` or `REFUSED` with each reason.
7. **On a pass,** your scan opens again with the camera where the fit put it, and your six features as the camera sees them. Each dot should sit on its feature. Press Done.

Tell an agent the summary block at the end, which is words and numbers only. Don't send the lines above it: they include the camera's place.

**If the fit refuses,** it names the worst feature. Re-click it on the camera page, or re-pick it in the scan, then paste:

```powershell
python -B C:\claude\home\.claude\worktrees\den-live\tools\den-live\register_now.py --again
```

That keeps the take, your clicks and your picks, so you only change what's wrong. A paste without `--again` records a new take and starts the clicks and picks afresh.

The summary tells the two kinds of refusal apart:
- It names the feature that sits furthest from the rest: re-click or re-pick that one.
- It says every feature fits with the features' own scale: that's the depth scale, which re-clicking won't fix. Tell an agent.

If an agent changes the depth scale in the method file, `--again` makes a new copy of the same take at the new scale and carries your clicks onto it. After a pass, `--again` with nothing changed just says it's already registered.

| Line | Means |
|---|---|
| `Depth corrected at the camera: x0.889 on all N frames.` | The camera service's correction is on; the paste goes on |
| `stopped: the camera's depth isn't corrected at its source ...` | Some frame says the correction is off, or has no record. Nothing was clicked. Tell an agent the line |
| `Floor guard: ... inside 5%` | The depth scale holds at the floor; the clicking goes on |
| `stopped: floor guard: ...` | The depth scale is wrong, or the camera can't see enough floor. Nothing was clicked. Tell an agent the line |
| `REGISTERED den-oak@...` | The camera is in your scan's frame again, written to the private `room-model.jsonl`. Step 3 reads it |
| `REFUSED` | The reasons name the features. Re-click or re-pick them and paste with `--again` |
| `Features' own scale: ...` | A cross-check, printed only: what scale the six features themselves would pick. Near 1.000 is good |

What's kept, all under `%USERPROFILE%\den-live\private\register\`: the take and its scaled copy (`takes\`), the camera's `/calibration` reply, the worksheet, your clicks, every `fit-N.json`, `room-model.jsonl`, and a `register-now-N.json` receipt per paste. Agents never open it. The scan page runs on 127.0.0.1 only, and reaches nothing beyond this PC.

### Tests

```powershell
python -B -X utf8 tools\den-live\test_register_now.py
```

- No camera, LA or ssh. A fake camera serves the den simulation's rendered marks take. den-register's own click session clicks where the simulation's fit clicked. The real scan page, on the authored room, is saved over HTTP with the simulation's room positions.
- The end-to-end part covers ten cases. Among them:
  - true depth registers;
  - the floor guard stops a wrong scale before any click;
  - the rigid fit refuses a scale the guard misses, and blames the scale;
  - one fit point picked 0.8 m off is blamed on that point, not the scale;
  - `--again` refits after one bad pick, makes a new copy when the scale changes, and says "already registered" when nothing changed;
  - a fresh paste starts the picks afresh;
  - a stopped click page, an unclicked feature and a den-register refusal each stop with their reason.
- It needs Node and a prepared den-sim set. It takes about a minute.

## Step 3: your marker, live, on this PC

Once step 2 has registered the camera, paste:

```powershell
python -B C:\claude\home\.claude\worktrees\den-live\tools\den-live\run_local.py
```

Then open http://127.0.0.1:18190/ and walk around the den. Ctrl+C stops it. While it runs, this PC reads the camera continuously, and your paste is the go-ahead for that.

It needs:
- step 2's registration;
- the camera token;
- the detector's venv;
- Node;
- the `claude/den-live-home` worktree beside this one, which holds Home's new people layer.

It says which one is missing if it can't start.

**What you see:** Home on your scan, with a marker for each person in the den.
- Each marker is a disc on the floor, with a ring showing how sure it is, an arrow showing which way the person faces (once the arms or hips show it), and a trail of the last 10 seconds.
- A coasting marker, someone briefly unseen, is dimmed.
- Someone who leaves is dimmed at once, while the tracker waits up to 6 s for them to reappear, and is gone within about 8 s.
- A small panel says what the stream says: the camera, how many people, how far behind, frames a second, and drops. A banner shows when something is wrong: "camera offline", "stream stopped", "depth not corrected at the camera", "detector errors", "too dark", or that the page shows another scan revision than the camera is registered to.

**What runs, all on this PC:**
- `source.py` fetches the camera's newest frame about 5 times a second.
- `detect_worker.py`, in the detector's venv with no network, runs YOLO26s and RTMPose-m on each frame.
- `perceive.py` runs den-observe and den-track, unedited, on step 2's registration. It checks every frame says the camera corrected its depth.
- `stream.py` serves the people stream on 127.0.0.1:8111.
- `live_page.mjs` serves Home from the `claude/den-live-home` worktree on 127.0.0.1:18190. It passes the stream through on the same paths step 4's gateway route will use.

**Its numbers:**
- **The floor:** the tracker's floor box is your scan's walkable floor, padded 1 m on every side. den-track's own box is a tape rectangle in no scan frame. In the den simulation, your real sofa sat outside it, and one reader split into three tracks.
- **Freshness:** a frame older than 1.5 s by the time the detector finishes is dropped and counted.
- **The clock:** frame times come from when frames arrive on this PC, because the den Jetson's clock reads 1970. They're kept on this PC's monotonic clock, so a time-sync step can't stall the tracker. "Behind" leaves out the camera-to-PC transit, about 0.1 s here.
- **Weak boxes:** a person box under 0.5 still reaches the tracker, but it never shows as a marker on its own. A track shows once a pose, or a box at 0.5 or more, has backed it (plan L3).
- **The arrow:** a heading older than 3 s is left off.
- **New epochs:** a camera restart gives new marker ids (`e2-trk-1`), never an old marker.
- **The scan revision:** it must be the one step 2 picked on. If the active scan changed since, it stops and asks you to register again.
- **Too dark:** the picture's mean brightness under 40 (of 255) for 3 s gives "too dark" until step 6's infrared.

**Every 10 s it prints one line**, for example `camera ok | 4.0 frames a second | 1 people | 0.25 s behind | 0 dropped a minute | 160 frames`. Anything dropped is named at the end, such as `, 3 out of order`. That's words and counts, never a position. Tell an agent those lines if something looks wrong.

**What's kept:** nothing. No frames, no tracks. Only the tracker's method copy and the detector's log go in `%USERPROFILE%\den-live\private\live\`.

### Tests

```powershell
python -B -X utf8 tools\den-live\test_live.py
```

- Most tests replay the den simulation's rendered reading take through the perceiver, with the rows the simulation's own detector run recorded. They need no venv, and they cover:
  - one reader is one marker, within 30 cm of where they sit;
  - a floor box that misses the seat splits them, and one that holds it doesn't;
  - frames without the camera's correction are refused;
  - the clock's epochs;
  - too dark;
  - an invalidated registration.
- The stream's tests check its events, heartbeat, Host check and client cap.
- The end-to-end test runs `run_local.run` with the real detector process, the fake camera and the page on the authored room. It needs the venv, Node and the `den-live-home` worktree, and takes about 40 s.
- Home's layer has its own tests on that branch: `node --test tools\test-apartment-people-layer.mjs`.
