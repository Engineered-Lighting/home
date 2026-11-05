# den-sim: the den lighting pipeline on a scripted scene

Feed a scripted scene through the den pipeline tools and watch the virtual spotlight in the
real Home swing to the couch. Then read the exact fixture command it would take, which is
shown and never sent.

**Status: a simulation.** No inference runs, nothing looks at a picture, and nothing reaches
a bulb, the camera, the Jetson, LA or Home Assistant. The red strip on the page says so.

It needs Node, Python and git, and no npm install to run. It has been tested on Node 24 and
Python 3.12, and `python` must be on your PATH: the harness stops and says so if it isn't.

## Run it (PowerShell 5.1)

From the folder that holds `tools\`:

```powershell
node tools\den-sim\run.mjs
```

Open http://127.0.0.1:18180/ and wait about 20 seconds for the two Feed buttons. Press Ctrl+C
in the terminal to stop.

With no household data at all, using an authored room and sockets:

```powershell
node tools\den-sim\run.mjs --room authored
```

## What a click does

**Feed: person A reading on the couch** does this, and the panel lists each hop as it finishes:

1. The pinned tools run on a scripted scene, for about 4 seconds.
2. The real lighting brain judges what they produced. It waits for 3 seconds of reading.
3. One fixture swings to the reader's hand point at about 30 degrees a second.
4. The exact command appears, marked `NOT SENT`.

**Feed: person A on the couch, not reading** runs the same tools with different typed answers.
The brain refuses, the spot stays off, and no fixture command is compiled.

A second click on the same button swings 0 degrees, because the beam is already there. **Reset**
reloads the page and clears the aim.

## What is real

The page's "What is real here" list is the source of truth, and this is its summary.

| Hop | Status | What runs |
|---|---|---|
| Input image, frames, detector | Scripted | `den-observe` synth writes a real-format take with blank RGB, and `fake-scripted-1` places the joints |
| Camera registration | Missing | `den-observe` refuses `den-register`'s output, so a synthetic entry stands in, at an invented place |
| Observations, tracks, bundles | Real, unscored | `replay.py`, `den_track.py` and `den_bundle.py route` with its fake sender, on open slots' proposed values |
| Bundle to Qwen | Missing | Not built. The bundles go nowhere |
| Qwen reply | Scripted | Typed answers through the real `fact_line`. No model runs |
| Room-level need, decide v0 | Real | den-core's real runner, on those typed lines. decide only logs |
| Person to aim | Adapter | About 40 lines in `chain.py` join room reading to the only track and assert `jev` and `deep_stack` health |
| Spot decision and aim | Real | The lighting brain under a fake clock, not the deployed one |
| Feed into Home, the motion | Adapter, scripted | This harness replays the brain's timeline re-timed to now. Home's follower has no motion, so 10 Hz steps are inserted |
| Home client, follower, overlay | Real | Unchanged `app/src` |
| Inverse kinematics | Real, fictional | `compile_target` on a fictional profile, so zero, direction, ratio and limits are invented |
| Plan and packets | Real, fictional | The codec with its public test key and a fictional context. The spot is a raw duty, because the semantic colour compiler refuses |
| ZHA envelope, CAN frames | Display only | A placeholder ieee, and an empty motor allowlist. No path exists to send either |

`den-take` capture, `den-register`, `den-label`, `den-n2` and `den-verify` do not run.

## Your own sockets and couch (optional)

Without these the page says `AUTHORED SOCKETS` and `DECLARED`. Both go in
`%LOCALAPPDATA%\den-sim\private`, stay outside git, and take effect when you restart.

For your sockets, log in to Home, open this path, and save the JSON there as
`registration-revision-3.json`:

```text
/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration/revisions/3
```

For your couch, copy your sofa mark:

```powershell
Copy-Item "$env:USERPROFILE\Downloads\victoria-den-sofa-target-v4.json" "$env:LOCALAPPDATA\den-sim\private\"
```

The mark only passes `tools/den-room/brain-targets.mjs` together with the export it names.
Without the export, its seat point is used with a stand-in zone. You can instead give a declared
point as `couch-declared.json`, holding `{"point_m": [x, y, z]}`.

## Where things go

Everything goes under `%LOCALAPPDATA%\den-sim`, outside every checkout:

| Folder | Holds |
|---|---|
| `tools\<name>-<sha>` | `git archive` of each pinned ref in `refs.json`, about 1 MB |
| `runs\<stamp>\click-<n>-<scenario>` | Each click's take, observations, tracks, bundles, den-core log, brain timeline and dry run, about 0.4 MB. Nothing cleans these up |
| `private` | Your optional files, and the brain geometry built from them |

To clear the runs:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\den-sim\runs"
```

## What keeps it off the network

- The harness binds `127.0.0.1` only and refuses any other `Host`.
- Anything it does not serve returns 503 and is logged.
- It refuses non-GET except a same-origin `/__sim/feed`, and destroys WebSocket upgrades.
- The page's security policy allows connections to its own origin only, plus `data:` and `blob:`,
  which Home's Photo view needs for local bytes.
- Every Python process here refuses all sockets, through an audit hook.
- There is no send control, token or route to Home Assistant anywhere.

## The test

It runs a real browser against the real harness on `--room authored`, with a scratch output
folder, in about a minute. It stays on this PC: it needs the pinned refs and the codec on disk,
so no CI job names it. Port 18180 must be free.

```powershell
npm ci --ignore-scripts
```

```powershell
node --test tools\den-sim\test-den-sim-browser.mjs
```

If Playwright says its browser is missing:

```powershell
npx playwright install chromium
```

## What this does not show

- That anything sees a reader. The joints and answers are typed, so this tests the plumbing.
- That the command would work. The codec on disk is older than the release `origin/main` pins,
  clock exchange is not generated, and no real radio has run this format.
- That the aim is right for a real person. Nothing measures head pitch, so the brain assumes level.
- That the numbers are tuned. Every tool runs unscored, on proposed values.

## Files

| File | What it is |
|---|---|
| `run.mjs` | The loopback harness that serves the unchanged Home |
| `feed.mjs` | Replays the brain's timeline into Home, inserts the motion, and holds the hop list |
| `brain.mjs` | Builds the geometry and runs one click from end to end |
| `chain.py` | The pinned tools, in their own guarded processes, and the adapter |
| `brain_run.py` | The real activity loop, publisher and brain under a fake clock |
| `dry_run.py` | The fixture command for the brain's target |
| `panel.js` | The strip and panel on the page |
| `refs.json` | The pinned refs and the codec's path |
| `test-den-sim-browser.mjs` | The honesty and safety test |
