# den-live

Tools for the live den view: the den camera, live, to markers and a virtual reading spotlight in Home. Steps 1 to 4 are here so far: the sofa check, registering the camera to your scan, your marker live on this PC, and the markers in your deployed Home from a service on LA.

## Step 1: the sofa check

Does the re-aimed den camera see a reader on the sofa? One paste and about three minutes of sitting answer it.

Run these commands from the repository root. The check needs the detector virtual environment, the camera token in a private file, and the deployment host's SSH alias. Configure `DEN_LIVE_CAMERA_TOKEN_FILE`, `DEN_LIVE_CAMERA_URL`, `DEN_LIVE_SSH_HOST`, and `HOME_CONTENT_PACKS_DIR` privately where they differ from the pinned helper defaults and the repository's standard content-pack directory. Keep token contents and operator values outside Git. Use `DEN_LIVE_CAMERA_ID` when the private registration uses a custom camera label.

The camera commissioning instructions live with the operator's pinned camera tooling. Release assembly reads its pinned Den dependencies from the commissioning checkout's object cache. `tools/den-sim/refs.json` identifies those commits. A clean public clone needs the reviewed dependency objects for release assembly.

### The check

With the room lit and nobody else in the den, paste:

```powershell
python -B tools/den-live/sofa_check.py
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
python -B tools/den-live/register_now.py
```

It needs the camera token (step 1's), Node on the PATH (the den simulation needs it too), and Python with numpy and Pillow.

1. **The empty den, 20 s.** It says "leave the den", waits 15 s, records 20 s, then says you can come back.
2. **The depth check.** Since 2026-11-11 the camera service corrects its own depth (x0.889), and every frame says so. If any frame doesn't, for example after a re-flash, it stops here. den-live adds no factor of its own. `REGISTER-LIVE-20261111.md` has why and what's left over: about 2% at the floor and at the far wall, in opposite directions.
3. **The floor guard.** It finds the floor in that depth and compares the lens's height with your 1.37 m tape. If they're more than 5% apart it stops here, before you click anything, and prints both numbers.
4. **Six clicks on the camera's picture.** That's den-register's own click page. Pick features that are spread out, at different heights and depths, solid and matte: two floor-level corners, two at table height, two high up (a door frame, a shelf). Avoid glass, screens and black fabric. Prefer features in the middle of the room over ones on the far wall: the depth scale is least sure out there. Each click shows the depth it found. All six need a click with depth; if a spot has none, pick another feature for that row. Remember what each one is.
5. **The same six in your scan.** Your scan opens on a local page, with the camera's picture beside it and your clicks numbered. For each row, orbit to the feature and click it once without dragging. Then Save.
6. **The fit.** den-register fits with the 0.10 m gate and prints `REGISTERED <camera-id>@<date>.<n>` or `REFUSED` with each reason.
7. **On a pass,** your scan opens again with the camera where the fit put it, and your six features as the camera sees them. Each dot should sit on its feature. Press Done.

Tell an agent the summary block at the end, which is words and numbers only. Don't send the lines above it: they include the camera's place.

**If the fit refuses,** it names the worst feature. Re-click it on the camera page, or re-pick it in the scan, then paste:

```powershell
python -B tools/den-live/register_now.py --again
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
| `REGISTERED <camera-id>@...` | The camera is in your scan's frame again, written to the private `room-model.jsonl`. Step 3 reads it |
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
python -B tools/den-live/run_local.py
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
- The live body (`body.py`) runs in the same detector process on the exact retained bundle. `body_link.py` keeps one body request in flight. The people stream starts without waiting for body setup.
- `stream.py` serves the people stream on 127.0.0.1:8111, and the body frames on the versioned upstream path `/body-v2`. The original loopback `/body` is also an alias in this producer.
- `live_page.mjs` serves Home on 127.0.0.1:18190: this checkout when it holds the body layer (`claude/den-live-body`), else the `claude/den-live-home` worktree. It passes the streams through on the same paths step 4's gateway route will use.

**The live body.** A tailnet Home view can show current measured gold particles and a torso-facing cue. The head and upper neck are removed before encoding. Missing surfaces are left empty. The figure clears when the track, privacy evidence, or measured depth is insufficient, or when its one-second capture deadline passes. Background learning uses eight seconds of confidently empty input followed by 12 qualified frames, in memory.

**RGB provider.** The standard service starts the provider when its `den-live-body-rgb.json` systemd credential matches the current registration receipt. The installer reads the optional profile beside the camera token and installs it under `/etc/den-live/private/` for systemd to load. An update without a supplied profile keeps the installed credential. Its fields are:

| Field | Commissioning value |
| --- | --- |
| `qualification_id` | Identifier for this qualified capture and head exclusion profile |
| `clock_evidence_id` | The `evidence_id` in the camera's [commissioned clock profile](../den-camera/README.md) |
| `transform_id`, `scan_revision` | Current camera registration and the scan it was registered against |
| `min_valid_depth_fraction` | Measured valid depth fraction required to use the registered profile, between 0 and 1 |
| `head` | `head_radius_m`, `neck_radius_m`, `max_head_shoulder_m`, `alignment_px`, `localization_m`, `max_motion_m_s`, and `max_separation_s` |

The service takes the depth correction factor and calibration hash from the registration receipt. Qualify the head sphere around the mean of the five RGB face landmarks and the upper-neck capsule running to the shoulder midpoint. The bounds must enclose the full region for anyone using the room, including when turning and reclining. Set the localization, alignment and motion values from that qualification. A clipped or incomplete stereo observation clears the figure.

The camera returns a capture age bound in the same authenticated response as the selected bundle. The service subtracts that bound from the start of its request and uses the result for the original one-second deadline. Install the matching camera release and qualify its profile before enabling the figure. The current RGB provider clears in darkness. A qualified infrared pose and depth profile is a separate input.

**Pipeline handoff.** Pass a `body_input` provider to `run_local.serve` or `run_service.run`. The live loop attaches `[session, rgb_seq, depth_seq]` from the parsed camera bundle to the detector request. It then calls `body_input(bundle, meta, depth, *, frame_key, mode, observations, tracks, supported, association)` after current RGB pose lifting and tracking. `supported` holds each track's observation IDs in `tracks` order. `association` maps track IDs to their association status and change time. In legacy RGB darkness `mode` is `none` and those collections are empty, so a qualified infrared provider can supply its own current pose and track for the same bundle.

The provider returns a `body_input.BodyInput` with the exact passed frame key, pipeline run ID and track epoch, registered scan revision and transform, qualified depth-profile ID, and earliest possible acquisition time of the paired inputs in the body lease clock. The existing camera packaging timestamp and arrival estimate are insufficient. The depth profile must supply current measured rays, with the sensor correction already applied. Return `None` for unqualified inputs.

For a person, include the current confirmed, confident track and its supported calibrated pose observation. For empty background learning, the provider must affirm that the region is empty and that the depth profile is healthy. Use `ambiguous` or `unknown` otherwise. Private `head_evidence` has `frame_key`, `binding` (`run_id`, `track_epoch`, `content_revision`, `transform_id`, `depth_profile`), `bounds_px` for the whole head and upper neck in rectified depth pixels, and the separate `alignment_px`, `localization_px` and `motion_px` uncertainty margins. Omit incomplete bounds. The body helper expands the bound and removes those rays before deriving the stream data.

The owner qualification uses one short tailnet phone run in room light and one with the qualified dark profile. Start empty, walk and turn, sit with a book, reach, recline, and leave. Cross with a second person and reconnect after expiry. Check each posture and direction cue, head exclusion, room alignment, marker continuity, and clearing. Record capture-to-paint, visibility and gaps by posture as local diagnostics.

**Its numbers:**
- **The floor:** the tracker's floor box is your scan's walkable floor, padded 1 m on every side. den-track's own box is a tape rectangle in no scan frame. In the den simulation, your real sofa sat outside it, and one reader split into three tracks.
- **Freshness:** a frame older than 1.5 s by the time the detector finishes is dropped and counted.
- **The clock:** frame times come from when frames arrive on this PC, as they did while the den Jetson's clock read 1970. They're kept on this PC's monotonic clock, so a time-sync step can't stall the tracker. "Behind" leaves out the camera-to-PC transit, about 0.1 s here. Since the Jetson's clock is right again, the stream's health line also has `transit_s_median`: arrival less the frame's `captured_unix_ns`, used only between 0 and 5 s. Step 4's walk test adds it to "behind".
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

## Step 4: markers in your deployed Home

Your markers move off this PC: an always-on service on LA, `den-live`, runs step 3's loop without the page. Home's gateway passes its stream through, so your phone sees the den, look-only, from anywhere on your tailnet with no sign-in. Home's side, the look-only view and the gateway's den-live route, is on `claude/den-live-home`, through its own pull request and LA's usual Home deploy.

In order:
1. The first check, read only, with someone in the den.
2. The install on LA.
3. Home's pull request and LA's Home deploy, from `claude/den-live-home`'s notes.
4. Four floor spots in your scan, once.
5. The walk test, then the empty den and the second person.

Every paste is one line in PowerShell on this PC. The ones that change LA print their undo line.

### 1. The first check

Before anything is installed, see what LA serves today and whether LA can pull the camera's frames fast enough. Do it with someone in the den, because the Lab's LA worker runs only then, and its load counts.

```powershell
python -B tools/den-live/first_check.py
```

It runs read-only commands on LA over one SSH (`--host` or `DEN_LIVE_SSH_HOST`), and pulls `/frame.npz` from LA for 60 s. The token travels inside ssh's input, never on a command line. Nothing is copied to LA, and no frame is kept. It prints:

| Line | Means |
|---|---|
| `Home checkout:` | LA's Home checkout: branch, commit, date, clean or not, and whether the gateway, `home-web-gateway`, is active |
| `Tailscale Serve:` | What port 443 forwards to, and whether `--proxy-protocol=1` is on. The look-only view needs Serve forwarding to 5182 with it on, which section 3 sets |
| `Content packs:` | The den's scan revisions on LA, the active one, and whether step 2's is there |
| `Frames from LA:` | Frames a second over the 60 s, median and 95th fetch time, median size, and any errors |
| `LA's CPU:` | Cores, the 1-minute load, and whether the Lab's LA worker (`lab-la-detector-trial`) is active |
| `For the install:` | Python 3.12 with venv, OpenCV's libGL and libglib, port 8111 free, and whether den-live is installed |
| `Jetson relay (plan pick 2):` | "not needed", or "needed" when LA gets under 4 frames a second or a median fetch over 300 ms |

Tell an agent those lines. They're words and numbers only: no address, hostname or token.

### 2. The install

This needs step 2's registration, the camera token, the detector's venv with its models, and this branch committed: the release names its commit, so it refuses while `tools/den-live` has changes that aren't committed. An agent commits them first.

```powershell
python -B tools/den-live/install_la.py
```

1. It refuses unless this checkout is on `claude/den-live` with nothing uncommitted, the only branch LA installs from, and prints the branch and commit it found. Then it refuses until step 2 has registered the camera.
2. It builds the release (`release.py`): this branch's `tools/den-live`, `tools/den-detect` and the unit at HEAD, the pinned trees the loop loads, the two models (YOLO26s `646f8bc3…`, RTMPose-m `5c0a4bf6…`), the pose model's manifest and rtmlib's wheel. Every text file going in is scanned for private strings: place and site names, tailnet names, private addresses, token-looking strings and the token itself. A hit stops it, naming the file and line, never the string.
3. It copies the tarball, `install.sh`, and four private files to LA's memory, in `/dev/shm/den-live-install/`, which only you can read. The four are the room model, the camera token, step 2's receipt, and a settings file with the camera's address and the den's site.
4. It runs `install.sh` there with sudo, so sudo asks for your password.
5. Whatever happened, it removes that folder from LA, and its own copies here.

`install.sh` does this on LA, and every step is safe to run again:
- It checks the tarball against its sha256, and every file in it against the release's own list.
- It unpacks to `/opt/den-live/releases/<id>/`.
- It builds the detector's venv in `/opt/den-live/venvs/<id>/`, with exactly `requirements.lock`'s versions: CPU torch from PyTorch's CPU index, rtmlib from its wheel, the rest from PyPI. That's about 400 MB the first time, and a few minutes. Then it loads the models once through the detector's own checks.
- It writes `/etc/den-live/config.json`: the camera, the gateway's content-pack folder, the venv, the models, port 8111.
- It puts the three private inputs, root-only, in `/etc/den-live/private/`.
- It installs `den-live.service` and starts it.
- It waits until the stream answers on 127.0.0.1:8111, then prints the undo lines.

Undo, to stop it and keep it from starting:

```powershell
ssh -t $env:DEN_LIVE_SSH_HOST "sudo systemctl disable --now den-live"
```

`install.sh` also prints a second undo line, which removes every file it installed.

**The service, on LA:**
- `den-live.service` runs as a dynamic user. Its private inputs come in through `LoadCredential`.
- It's held to `MemoryMax=3G` and `CPUQuota=150%`, and restarts 10 s after any stop.
- It may reach only this host's loopback and the camera's one address.
- The people stream binds 127.0.0.1:8111 only. The detector process still refuses Python sockets itself.
- It refuses to start, with one sentence in its journal, when an input is missing or doesn't match. Then systemd doesn't restart it, because a restart can't mend that. That's a missing credential, a receipt that didn't register the room model's camera, another scan revision than step 2 picked on, or a camera that no longer fits the registration.
- Every 60 s it writes one line of words and counts to its journal, the same line as step 3's: `ssh $env:DEN_LIVE_SSH_HOST "journalctl -u den-live -n 5 -o cat"`.

**What's kept on LA:** what step 3 keeps, in its state folder, `/var/lib/den-live/`: the tracker's method copy and the detector's log and caches. The log is rolled once past 10 MB. No frames and no tracks. The release, the venv and the models are under `/opt/den-live/`, and the settings and private inputs under `/etc/den-live/`.

### 3. Home

Follow the [Full Home rollout run sheet](../../docs/FULL-HOME-SERVE-RUN-SHEET.md) to enable the dedicated Serve listener, then test the phone and desktop paths. If restoring an existing look-only deployment, restore its recorded gateway environment and Serve mapping together.

### 4. Four floor spots, once

```powershell
python -B tools/den-live/walk_test.py --spots
```

Your scan opens on a local page, step 2's scan picker without the camera's picture. Click 4 floor spots you can find both in the room and in the scan, such as a rug corner or a sofa foot, spread over the floor the camera sees. Note which is which, then Save. They go to `%USERPROFILE%\den-live\private\walk\spots.json` with the scan revision they were picked on. Paste the line again to change any of them.

### 5. The walk test

With the room lit and nobody else in view:

```powershell
python -B tools/den-live/walk_test.py --home https://<your Home address>
```

It reads the people stream through your Home's tailnet address with no cookie, as your phone does. It says "Stand on spot 1." and so on. You have 6 s to get there, then stand still for 10 s. Then it prints:

| Line | Means |
|---|---|
| `Distance from spot: 0.18, 0.22, 0.31, 0.12 m` | How far your marker's median position was from each spot. The goal is within 0.5 m |
| `Lag, capture to stream: median …, 95th …` | From the camera's capture to the stream, the link between the homes included. The goal is about 1 s. In brackets: the camera-to-LA part, and LA's part |
| `Note: spot 3 showed 2 markers …` | More than one marker, or none, during a spot |

Then, when you like:

```powershell
python -B tools/den-live/walk_test.py --home https://<your Home address> --empty
python -B tools/den-live/walk_test.py --home https://<your Home address> --second
```

- `--empty`: leave the den when it says so, for 10 minutes, until you hear "done". It prints `Empty den, 10 minutes: 0 markers` (goal 0), or how many showed and for how long.
- `--second`: stay in, and have someone else come in. It says "two" out loud once it has shown two markers together for 2 s, then prints how soon and for how long.

Tell an agent the printed lines. Your Home's address is never printed or kept.

If it stops at once: "asked for sign-in" means your Home doesn't see this PC as a look-only tailnet device. That's section 3's env lines, or Serve not forwarding to 5182 with `--proxy-protocol=1`. "No den-live route" means Home isn't deployed yet. A 502 or 503 means den-live isn't running on LA.

### Tests

```powershell
python -B -X utf8 tools\den-live\test_service.py
```

- No camera, LA, Home or ssh. Fakes on 127.0.0.1 stand in for the camera, the people stream and LA's half of the first check.
- The camera's paths: any path but `/frame.npz` and `/calibration` is refused before a request is made. Every tool gets its camera client from one place. The fake camera logs every path, and each end-to-end test fails on any other.
- The service runs end to end against the fake camera with the real detector. Its credentials are in a temp folder, and the stream serves the reader. Its refusals are each one sentence.
- The release builds a tarball whose files, sums and sha256 match, the same bytes each time, and it refuses a planted private string.
- `install.sh` passes `bash -n`, and shellcheck when it's installed. Its pinned hashes are the lock's, and it refuses before changing anything.
- `install_la.py`'s copies, install and clean-up run through a fake transport, the clean-up on failure too.
- The first check's lines come from canned command output, and its LA half runs here with no system tools.
- The walk test's distances, lag, empty den and second person run against a fake stream.
- The scan page's spots mode runs on the authored room.
- The service's end-to-end test needs the venv and a prepared den-sim set. The release's needs the venv's models and rtmlib's wheel. The scan page's needs Node.

## Labels for your den

You label the den's objects in Home: the sofa, the desk, each painting, the 3D printer. den-live keeps the one document (`den-room-labels-1`, `room_labels.py`) and serves it on the stream server: `GET` and `PUT /api/den-live/v1/homes/den/labels`. A save replaces the whole document, a compare-and-swap on its revision. The lighting coordinator reads the labels in this process (`Store.confirmed()`), and the Qwen ask gets one line about the seated person from `line_for()`, for example "Seated at the desk, beside the 3D printer.".

### Step 1: label your den, on this PC

```powershell
python -B tools/den-live/run_local.py --labels-only
```

It starts only the labels store and Home on your scan, from the `claude/den-labels-home` worktree. No camera, detector or registration is needed. Open the page it prints, go to the den's view and press **Labels**. Click something in the scan, type its name, fit its box, then **Save**. Ctrl+C stops it.

- **Where they're kept:** your private den-live folder, `labels\room-labels.json`. `room-labels-history.jsonl` beside it holds the last 100 saves.
- **A file that won't read** is set aside as `room-labels.bad-<time>.json`, and den-live starts with none rather than refusing.
- **The live loop serves them too.** `run_local.py` without `--labels-only` serves the same labels beside the markers. The LA service keeps its own copy in its state folder, `labels\`.
- **To LA, once.** `install_la.py` brings these labels when they read. On LA they're a root-only credential, and the service copies them into its own `labels\` only while it has none. After that you label in deployed Home, and a later install never overwrites LA's labels.

### Step 2: what's near you, under your marker

With the live loop running (step 3 above), each track in the people stream carries `near`: what's near it from your confirmed labels, for example "at the desk, beside the 3D printer". It comes from the same `near_phrase()` the Qwen line uses. Home shows it under your marker. It's null when there are no labels or none near, or when the labels were placed on another scan revision than the one den-live is registered on.

### The line for the Qwen ask

When the Qwen ask runs (below), `serve()` gives it `room_labels.near_line(store, scan revision)` as its `near_line` hook. Each ask about one seated person reads the labels as they are then: a save in Home reaches the next ask. It sends `line_for()`'s line as `near_person` while `labels_to_qwen` is on, and logs the labels' revision with it.

### Tests

```powershell
python -B -m unittest test_room_labels
```

(from `tools\den-live`). They cover:
- the record's checks, which use the same refusal words as Home's `home-den-labels-core.mjs`;
- the store's compare-and-swap, its atomic write, its history and the set-aside file;
- the line from where a person is, and the ask's hook;
- the two routes, on a real loopback server.

## The Qwen ask (inference plan, Part 1)

den-live asks Qwen what someone in the den is doing, but only once Frigate has noticed someone. With nobody there, Qwen is never asked.

**When it asks.** All of these must hold:
- Frigate's den occupancy sensor reads on in the den home's Home Assistant (`occupancy.py`).
  - It's read twice a second and held for 2 minutes after the sensor last read on, den_lighting's own vacancy, because Frigate can lose a reader sitting still. Standing up still stops the asks at once, through the seated check.
  - If it reads unknown, or nothing reads for 10 s, nothing is asked.
  - The reader never follows a redirect, so the token goes to Home Assistant only.
- A confirmed track is seated (`seated.py`: the coordinator's `body.seated()`, copied unchanged until den-live carries its code).
  - Becoming seated needs 2 s of hips 0.30 to 0.75 m above the floor (or the face below 1.30 m with no pair of hips) and a torso moving under 0.15 m/s.
  - Staying seated needs only low hips, so shifting along the sofa doesn't drop you.
- The settings say `asks_on`, and the back-off monitor is below its step 2.
  - At step 1, it asks at most every 4 s.
  - If `backoff_status` is set but the monitor's file is missing, unreadable or over 10 minutes old, it counts as step 1.
- The gate is checked again just before each question is posted.

**The pace.** One ask at a time, no sooner than 2 s after the last one, and at most 30 a minute. After a failure it waits 30 s.

**What it sends** (`den_live_scene.py`, prompt `den-live-scene-2`):
- Three stills from the last 3 s, made into JPEGs in the detector's process (`stills.py`).
- Today's Lab question, with three changes:
  - "hobby" and "craft" are added;
  - the old camera placement is removed;
  - `near_person`, the labeling plan's line, is sent while `labels_to_qwen` is on and exactly one person is tracked.

  `test_ask.py` holds the rest to the Lab's pinned `qwen_scene.py`.

**What it keeps.** One line per ask in `days/<UTC date>/asks.jsonl` in the service's state folder, kept 14 days. It holds:
- words and numbers only: no image and no reply text;
- each still's capture and arrival times;
- when the ask was decided, sent and answered;
- the model, its token counts and the parsed answer;
- the labels fields.

**What it gives.**
- **The stream:** `health.qwen {state, why, last_reply_at, reply_s_median, asks_per_min, backoff_level}` on every event.
  - Each track it asked about gets `activity {word, close_up, at}` for 30 s, but only when den-track was sure, through the stills' window, whose answer it was.
  - The state reads `unavailable` after 3 failed questions in a row, or 3 sets of pictures that didn't come.
- **In-process:** each answer goes to `Asker.on_reply` hooks as `{"fact": <la-fact-log-1 line>, "ask": <the log line>}`.
  - The log line carries `association {safe, status, since_at}`: whether den-track held the asked track confident through the stills' window, and since before it.
  - The end-to-end plan's person state holds a reply that isn't safe.

**Turning it on (inference plan, step 3).**

1. **In the den home's Home Assistant, signed in as you:**
   - make a user `den-live`, an admin (the coordinator chat's decision 08, since only an admin can follow den_lighting's events);
   - as that user, make a long-lived access token. It's the same token the coordinator's link uses.
2. **On this PC, in PowerShell: save the token and the address once,** beside the camera token. Copy the token from Home Assistant first, then paste:

   ```powershell
   Get-Clipboard | Set-Content -Encoding ascii -NoNewline (Join-Path (Split-Path $env:DEN_LIVE_CAMERA_TOKEN_FILE) "den-live-ha-token.txt")
   ```

   ```powershell
   Set-Content -Encoding ascii -NoNewline (Join-Path (Split-Path $env:DEN_LIVE_CAMERA_TOKEN_FILE) "den-live-ha-url.txt") "http://<Home Assistant's IP address>:8123"
   ```

   - **The address:** Home Assistant's as LA reaches it, over the private link between the homes, the same way den-live already reaches the camera.
     - It must be an IP address, since the unit allows the service that one address.
     - Use `http://`. With `https://`, Home Assistant's certificate would have to name that IP address, or the check fails every read.
   - Neither file goes anywhere but LA, and the install never prints them.
3. **Install den-live as in step 4:** the same `install_la.py` paste. With both files there, it says "The Qwen ask: on". It then:
   - copies the token as a credential;
   - adds the asks block to `/etc/den-live/config.json`;
   - adds the address and the credential to the unit's drop-in.

   Without the files it says "off", and an install without them removes an old token from LA.
   - The release is built from this branch's last commit, so a change only reaches LA once it's committed.
4. **The check:** paste this on this PC, then sit, read, use your phone and leave, as it speaks the cues:

   ```powershell
   python -B tools/den-live/ask_check.py --home https://<your Home address>
   ```

   It prints, for example:

   ```
   Ask check, 20:15
   Read (2 min):   Frigate on before the cue, seated and asking +2.6 s, first answer +3.3 s, first close-up +3.3 s
                   close-up on 38 of 40 answers (reading 38, unclear 2)
   Phone (2 min):  close-up on 0 of 40 answers (phone_use 37, unclear 3)
   Leave:          asks stopped 1.2 s after the cue
   Answer time:    median 0.62 s, as den-live reports it
   Stream:         480, 478 and 120 events in the three parts, 0 reconnects
   ```

   The numbers are made up, to show the shape. Tell an agent the lines: they're words and numbers only.

**The settings it writes on LA.** The asks block in `/etc/den-live/config.json`:

```json
"asks": {"ha_url": "http://<den home's Home Assistant IP>:8123", "ha_entity": "binary_sensor.den_oak_person_occupancy",
         "vllm_url": "http://127.0.0.1:8000", "asks_on": true, "labels_to_qwen": true, "min_interval_s": 2, "max_per_minute": 30}
```

- **Switches survive a reinstall:** a reinstall keeps `asks_on`, `labels_to_qwen` and the pace as they were in the file it replaces, so an ask you switched off stays off.
- **Two keys a hand edit can add:**
  - `time_zone`, an IANA name: the context's time label uses LA's own zone without it;
  - `backoff_status`: the back-off monitor's status file. Today the monitor keeps it in a folder den-live's service user can't read, so the install doesn't set it, and den-live's own limits are its guard.
- **The credential:** the token is `ha-token.txt` among the service's credentials.
- **Without the block, or with a bad one,** the ask is off and the journal says why in one sentence. That includes a token that's missing and an ask that fails as it starts. The markers go on either way.
- **Changing a switch:** `asks_on`, `labels_to_qwen`, `min_interval_s` and `max_per_minute` are re-read whenever the file changes, so no restart is needed.
  - A block taken out, or a value that doesn't read, turns the asks off until it reads again. The health line says why.
  - The pace can't go past AGENTS.md's bound: `min_interval_s` 2 to 60, `max_per_minute` 1 to 30.
- **The unit's drop-in, written by the install:**
  - a `LoadCredential` for `ha-token.txt`;
  - an `IPAddressAllow` for Home Assistant's address.

  Both are only there while the asks block is. `install.sh` refuses settings that set the ask up without a token, and a token without such settings, so the unit can't be left waiting for a credential that isn't there.

### Tests

```powershell
python -B -m unittest discover -s tools\den-live -p "test_ask.py"
```

- Stand-ins for Qwen, Home Assistant and the detector. No camera, no host and no picture of anyone.
- **The question:** only the planned changes from the Lab's pinned question, a stable body, strict replies, and the Lab log's exact keys.
- **The gate:**
  - no ask while Frigate says nobody or unknown, or while standing;
  - about every 2 s while seated;
  - the 30 s back-off, and "Qwen isn't answering" after 3 failures;
  - the switch, and both of the monitor's steps.
- **The label line:** sent for one person only, with its switch on, and a bad line doesn't stop the ask.
- **The detector's channel:** stills replies never reach the rows' queue.
