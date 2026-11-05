# Rung 8 plan: the den light model

Status: plan only. Nothing here is built or run yet. No host, camera or light
has been touched to write it. Revised on 2026-11-04 after an independent
review, on 2026-11-05 after a second check, again on 2026-11-05 after a
final check (the install no longer writes over the camera script that
perception-lab pins), and on 2026-11-05 after two reviews of the pushed Stage
0 (an unhealthy camera is told apart from an unreachable one, the rollback
check asks the camera again, a dim den is refused before the hold, pilot
commands wait for the rollback, and a second Ctrl+C cannot lose a record).

The plan has two stages. **Stage 0** (about 3 build days, then about one
minute of camera time) checks that the camera can meter light at all. **Stage
1** (about 5 build days, then one probe session) is built only if Stage 0
passes.

## Context

Rung 8 predicts what any scene of the den's four Hue bulbs does across the
camera's view of the room, replacing aim math, beam traces, hand-drawn
footprints and the virtual den's one-bounce sum. Rung 9's coordinator needs it
to predict before it acts. The bulbs are den_lighting's `LIGHTS`:
`light.den_front_left`, `light.front_right`, `light.den_rear_left` and
`light.den_rear_right`.

The rung asks for a per-channel basis fitted from camera captures in the empty
den, tested on held-out mixed scenes it was not fitted on. **Gate: relative
error against camera estimates, median 8% and p90 19%.** Probes follow rung
7's rule: den_lighting paused, probes logged as non-human changes and kept out
of corrections, normal scene written back before
`den_lighting.resume_automatic`, abort on occupancy or on any change the run
did not send.

The basis and the scores come from the same camera, so its scale (k_L) and
surface reflectance (ρ) cancel. The score is relative: it does not wait on
rung 7's absolute anchor, and it cannot detect a camera-scale error. Absolute
lux waits on photometry-commissioning. The occupied visibility term is a later
step. Neither stage depends on rungs R, N1 or N2.

## Run folders

Each run writes only to its own folder on the PC: `C:\den-light\stage0-<id>\`
for Stage 0 (the install's pasted lines, the drop-in and `install.json`, then
one `sweep-<n>\` folder per sweep) and `C:\den-light\<session-id>\` for each
probe session. Like den-take, the tools refuse a folder inside a git checkout
or OneDrive, a Stage 0, sweep or session folder that already holds a sweep or
session, and a PC low on disk; each refusal has its own test. Frames are never
copied to git, OneDrive or the cloud. The HA token lives in
`C:\den-light\private\` and the camera token in den-take's private folder
(below); no code path prints or writes either token.

## Stage 0: can the camera meter light?

The biggest risk is the camera, not the model. The ISP's output may not turn
back into linear light (local tone mapping, for example), or DepthAI on the
Jetson may not hold white balance or report gain per frame. Stage 0 finds out
before any of Stage 1 is built.

**The installed camera.** The den's OAK-D Pro is served by `oak-den.service`
on DepthAI 3.10.0. Its server as installed is copied at
`tools/fixtures/installed-oak-capture-20261016.py`, sha256
`d052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132`.
perception-lab pins that file by path and sha256: `bundle_rgb_input.py`'s
`CAMERA_SOURCE` (`victoria-camera-pilot/pilot-20260926/capture.py` under the
Jetson account's home) and `CAMERA_SHA` (the fixture's hash). On any mismatch
it refuses every bundled-RGB capture (`fixed_camera_clock_checks_changed`), so
the install never writes to that file or its directory (Run, step 1). As
the fixture is written (it sends no camera control, and its strict argument
parser takes no focus flag), the RGB camera runs automatic exposure, white
balance and (if fitted) autofocus with no control input, and each frame
reports only `exposure_duration_seconds`. The fixture check (Run, step 1)
confirms that the live ExecStart runs the pinned file, and with which flags,
before this is relied on: a drop-in could start another script that locks
autofocus.
Its 640x360 NV12 output is tone-mapped and gamma-encoded by the ISP (no RAW),
and shares one sensor, ISP and exposure with the 1920x1080 RTSP stream
Frigate reads.
The IR projector and flood are off. `/frame.npz` returns 503 when stale
(15 s); bundle metadata is schema 1. Rung R's den-take token file,
`C:\den-takes\private\camera-token.txt`, is reused; den-take's code is not.

### Build (offline, no approval)

New files live in `tools/den-light/`: standard-library Python 3.12, on a
Windows PC with PowerShell 5.1 unless the table says otherwise.

| File | Runs on | Does |
|---|---|---|
| `METHOD-V1.md`, Stage 0 part | repo | `meter-norm/v1` and the Stage 0 check in one fenced block the code reads, with the named statistics below. Committed before any sweep frame. Every output records its sha256, as rung R's CHECK-V1 does. |
| `oak-capture-meter.py` | Jetson | The installed server plus the metering hold, and nothing else. Installed beside the pinned script, never over it. |
| `meter.py` | PC | Pure: reads bundles (zipfile and a small .npy reader, as den-take does), decodes NV12, applies `meter-norm/v1`, fits the inverse response, runs the global check. |
| `sweep.py` | PC | The Stage 0 runner: holds the camera through `POST /meter` with den-take's camera token, steps exposure, releases, writes `response.json`. `--prepare` checks the fixture-check lines and writes the drop-in; `--install-record` checks the after-check lines and the camera, then writes `install.json`; `--rollback-check` checks the rollback lines and the camera; `--recheck` recomputes a saved sweep's verdict from its folder. Never calls HA; writes no lights. |
| `README.md`, Stage 0 part | repo | The owner's PowerShell 5.1 lines for approval 1, the rollback, and the privacy rules. |
| `test_den_light.py` | anywhere | All tests, synthetic data only. |

**What Stage 0's METHOD-V1 fixes.** Each statistic is named here, so METHOD-V1
only fills in its number:

- the fixed white balance (kelvin) and the clipping limit per channel (a high
  code value), both part of `meter-norm/v1`;
- **noise tolerance**: two frames agree when every region of a 4x4 grid has
  per-channel means within N code values of each other. The still-scene wait
  and the repeat check use it, and Stage 1 reuses it;
- **still-scene wait limit** (about 20 s) and **run limit** (about 100 s,
  from the countdown's start to the release);
- **residual**: the per-channel RMS of fit minus observed, in code values, at
  most R;
- **region test**: the response is also fitted separately on each region of a
  4x4 grid. A fit by alternating least squares is fixed only up to scale, so
  each region curve is first scaled to the global curve by least squares on
  their ratio, over the codes from 16 to 235 that the region supports (at
  least a minimum count of the region's samples at that code). It then lies
  within P% of the global curve at each of those codes. A region is not
  pinned at code 128, which it may not reach. A region that supports no code
  from 16 to 235 is reported as untested and does not fail;
- **gain agreement**: each held frame's reported exposure is within one
  frame-time quantum (the step the camera reports exposure in, which METHOD-V1
  records) of the hold, and its ISO and white balance kelvin equal the hold
  exactly;
- **sampling and timing**: the pixel step (every n-th pixel each way), the
  frames kept per exposure, the settle limit (how long a step may wait for
  its first agreeing frame), the still-scene span, the camera's exposure and
  ISO limits, the fit's iteration count and smoothing weight, and the minimum
  samples per code.

**`meter-norm/v1`** (the normalisation version A9 and E7 ask for) works on
one pixel of one frame: decode NV12 as BT.601 limited range, as the server
does for its JPEG; flag the pixel if any channel reaches its clipping limit (a
flagged pixel has no value in that frame); otherwise apply the measured
inverse response per channel, scaled so that f(128) = 1, divide by t·G
(exposure in ms times ISO/100, from the frame's own metadata) and combine R, G
and B with fixed weights. White balance is held at one fixed value in every
capture and is part of the version. meter-norm/v1 does not say which pixels a
reading averages: Stage 0's response fit drops a pixel only from the frames
that flag it, and Stage 1's reduction rule (The model) sets the session mask.
So the meter-norm version Stage 0 records is the one Stage 1 records. The
black level cancels, because each Stage 1 contribution is a frame minus an
all-off frame at the same exposure. A rule change means a new version and a
refit. Rung 7 later reuses it for its camera scale.

**The metering hold** in `oak-capture-meter.py`:

- An authenticated `POST /meter` sets manual exposure and ISO, the METHOD
  white balance, and the current lens position if the module focuses, with a
  time to live of at most 120 s. The PC asks for 30 s and renews every 10 s.
  POST always needs the token, even on loopback.
- `POST /meter/release`, expiry and a server restart return the camera to
  exactly three modes: auto exposure on, white balance AUTO, and focus as the
  installed server leaves it. Under the fixture, which takes no focus flag,
  that is CONTINUOUS_VIDEO (not `AutoFocusMode.AUTO`, which focuses once and
  stops). Release and expiry send those three; a restart (of the process or
  of its capture session) holds nothing and sends no control, so the new
  pipeline starts in DepthAI's defaults, which are the same three. If the
  fixture check finds a live ExecStart that locks autofocus, the rebuild
  restores that lock instead, so restoring never changes installed behaviour.
  A crashed PC tool can never leave the camera held.
- Each RGB frame adds the gain, white balance and lens position it reports,
  plus `meter: {held, generation}`, inside its `frames.rgb` entry only
  (perception-lab's bundle readers require `frames` to hold exactly rgb,
  depth, left and right). The schema stays 1, keys added only; den-take's
  `parse_metadata()` ignores extra keys.
- Nothing else changes. A test checks that the file equals the installed
  fixture outside its marked meter blocks. perception-lab's bundle tests keep
  loading the unchanged fixture; they cannot load this file, because
  `test_lab_bundled_rgb_stereo.py` raises unless the file it loads hashes
  `CAMERA_SHA`.

**The response fit and the global check.** `meter.py` fits a monotone inverse
response per channel: alternating least squares on a piecewise-linear curve
with a knot at every code, made monotone with pool-adjacent-violators. The
irradiance step weights each sample by a hat over codes, and the knot step
adds a light smoothing penalty: with every step the same 1.41x, the data
alone fix the curve only up to a ripple that repeats every step. The fit
fixes the curve only up to scale: the global curve is scaled so that
f(128) = 1, and each region curve is scaled to the global curve by least
squares before the region test (above). The global check is the residual and
the region test as METHOD-V1 names them: one curve must fit the whole image
within R, and the same fit on each region of the 4x4 grid must stay within
P% of it.

**`sweep.py`** reads auto-exposure's pick (t, G) from a few auto frames, then
holds and steps t·G from 1/16 to 2 times that pick in steps of about 1.41x
(11 exposures). It changes time, not gain, unless time would pass the 30 fps
frame time. Before any hold it checks that the top step fits the camera
(t·G at most the time limit times the ISO limit, over 2: with METHOD-V1's
numbers, auto at or below ISO 800 at 33 ms). A dimmer den is refused with a
plain "too dark: …" that says to turn on one more den bulb at full: nothing
is held or written, and it is not a void, so it does not use up a rerun. It
checks the camera's first auto frame the same way before the countdown. It
repeats the first exposure at the end; if the two disagree beyond the noise
tolerance, the scene moved and the sweep is void (see Void sweeps). It
releases on success, error and Ctrl+C. Ctrl+C or an error before the
repeat's frames are in voids the sweep. The hold phase ends at the release,
on every path; from there Ctrl+C is ignored until `response.json` is written,
so a second Ctrl+C during the release or a void's cleanup cannot lose the
record or keep the frames. A void records whether `frames\` and
`frames.jsonl` are really gone. An error after the data is complete (in the
fit) is recorded as outcome error with the frames kept, and `--recheck`
recomputes the verdict from the saved folder. It writes to a new
`sweep-<n>\` folder in the Stage 0 folder (Run folders, above), and refuses to
start unless that folder's `install.json` exists and its after hash equals
`oak-capture-meter.py`'s committed sha256.

RAW output is the fallback: a bigger change with more USB load, and the server
ends its session if its RTSP writer falls about 2 s behind. Locking
auto-exposure where it is, the mono cameras and a second camera process were
rejected (less control; no colour and near-infrared; the server is the one OAK
owner).

**Stage 0 tests:**

- NV12 decode round-trips; the response fit recovers a known S-shaped curve
  within 1% and its residual matches a hand-worked RMS; a synthetic local tone
  map fails the region test while one global curve passes it; a region curve
  at another scale passes once scaled; a pixel is dropped from the fit only in
  the frames where it clips; gain agreement passes an exposure one frame-time
  quantum off and fails one two quanta off, or any ISO or kelvin difference.
- `sweep.py` against a fake camera server: the exposure list, renewal,
  release on success, error and Ctrl+C, and no HA call. One test per void
  reason (repeat mismatch, still-scene wait over the limit, a 503, an
  unreachable camera, a new camera session, a run over the run limit, and
  interrupted or errored before the repeat, once by Ctrl+C and once by a
  refused `POST /meter`): each writes outcome void with that reason and
  deletes the sweep's frames, and `frames_deleted` is false when a delete
  fails. Ctrl+C after the repeat does not void it, and a second Ctrl+C during
  the release or a void's cleanup keeps the record. A too-dark pick is
  refused before any `POST /meter`. A 503 is reported as an unhealthy camera
  (with the rollback advice once the drop-in is installed), a failed
  connection as a network problem. It refuses without `install.json` or with
  a wrong after hash. `--recheck` matches the saved verdict.
- Run folders, one test each: a folder inside a git checkout, one inside
  OneDrive, a Stage 0 or sweep folder that already holds a sweep, and a PC
  low on disk each refuse and write nothing.
- The install lines: `--prepare` refuses a wrong hash or an ExecStart that
  runs anything but the pinned file, and writes a drop-in that keeps every
  flag; `--install-record` refuses changed flags, an extra drop-in, a changed
  pinned hash, a wrong meter hash or a held camera; `--rollback-check`
  refuses anything but the before state, an unhealthy or unreachable camera,
  a camera that restarts within the wait, and bundles that still carry the
  meter keys.
- The camera server against a fake control queue: request checks, the token
  rule, renewal, expiry and release. Release and expiry send exactly auto
  exposure on, white balance AUTO and focus CONTINUOUS_VIDEO (or the installed
  lock, if the fixture check found one); a restart starts unheld and sends
  nothing. The new keys go inside `frames.rgb` only. The file matches the
  fixture outside its meter blocks.

### Run (approval 1)

1. **Install the server** (approval 1). Nothing is overwritten: the meter
   server goes in a new sibling release directory, and a systemd drop-in
   points `oak-den.service` at it with its flags unchanged.
   - **Fixture check.** Read `oak-den.service`'s FragmentPath, DropInPaths and
     effective ExecStart with `systemctl show` (not `systemctl cat`, which
     would also print any inline `Environment=` secret), and `sha256sum` the
     pinned file. `sweep.py --prepare` checks that ExecStart runs exactly the
     pinned file, `victoria-camera-pilot/pilot-20260926/capture.py`, by
     absolute path, and that it hashes the fixture's `d052fc21…`. On a
     mismatch, stop and change nothing. A new fixture is then its own piece
     of work: pull the installed copy into
     `tools/fixtures/installed-oak-capture-<date>.py`, record its sha256 in
     this plan, rebuild `oak-capture-meter.py` from it and rerun the
     outside-the-meter-blocks equality test. It also means a perception-lab
     change, outside rung 8 and with its own approval: `CAMERA_SHA` (and
     `CAMERA_SOURCE`, if the path moved) in `bundle_rgb_input.py`, the
     fixture path that `test_bundle_rgb_input.py` and
     `test_lab_bundled_rgb_stereo.py` hard-code, and the copy of the hash in
     `test_bundle_rgb_clock_uncertainty.py`. Then ask for approval 1 again.
   - **Install.** `--prepare` also writes the drop-in
     `zz-den-light-meter.conf`: `ExecStart=` to clear the old line, then the
     before ExecStart with only the script path changed to
     `victoria-camera-pilot/pilot-<date>-meter/oak-capture-meter.py`, every
     flag kept. The owner makes that directory (it must not exist yet),
     copies `oak-capture-meter.py` and the drop-in into it, installs the
     drop-in as `/etc/systemd/system/oak-den.service.d/zz-den-light-meter.conf`
     (its name sorts last, so it wins over any other drop-in), runs
     `daemon-reload`, then restarts `oak-den.service` while the den is empty
     and dark. The pinned file and its directory are never written.
   - **After-check.** `sweep.py --install-record` reads the same
     `systemctl show` lines and both hashes after the restart. It checks that
     ExecStart is the before line with only the script path changed, that the
     drop-in is the only new one, that the meter file hashes
     `oak-capture-meter.py`'s committed sha256, that the pinned file still
     hashes `d052fc21…`, and that `/health` answers and a bundle shows
     `held: false` with the new keys. Then it writes the before and after
     hashes, both ExecStart lines, the paths and the drop-in to
     `stage0-<id>\install.json`. On any mismatch it writes nothing, and the
     owner rolls back. The camera part tells two failures apart: a request
     that cannot connect is a network problem (den-take's ssh tunnel), while
     a camera that answers unhealthy (a 503) is reported with its status and
     told to roll back now, since the meter build is then the suspect.
   - **Pilot commands wait for the rollback.** The pilot's own tools do not
     work while the drop-in exists. The local pilot tool's `inspect()`
     refuses its check, activate and rollback when `oak-den.service.d` holds
     anything besides its own `90-` file. The pilot-release helper
     (`switch-pilot-release.sh`, on the owner's PC only, not in git) passes
     `--af lock`, `--imu`, `--npz-encoding` and `--check-config`, which the
     fixture's parser rejects, and the drop-in would override whatever
     release it switches to. So the owner runs the den-light rollback, and
     sees `--rollback-check` pass, before any pilot command.

   Frigate's den feed drops briefly during the restart; nothing else should
   change. Rollback (on a no-go, an unhealthy camera, before any pilot
   command, or whenever the owner asks): remove the drop-in, `daemon-reload`,
   restart once more, then the same after-check, now expecting the before
   state. `sweep.py --rollback-check --camera` expects the before ExecStart
   and drop-ins and the pinned hash, then waits past the restart: `/health`
   must answer healthy and still name the same camera session about 30 s
   later (a crash-looping `Restart=always` unit still reads `active` right
   after a restart), and one bundle must come without the meter keys. Only
   then does it write `rollback.json`. The meter directory is then inert and
   may be deleted. After a pass
   the meter server may stay for Stage 1, which needs it; unheld, it behaves
   as the installed server.
2. **Light the room** with den_lighting's arrival scene, at least one bulb on.
   The tool writes no lights. If no bulb is on, the owner turns one on in the
   HA app, and den_lighting keeps that as its normal scene until it is changed
   again (`policy.py` `manual()`); set it back by hand afterwards if wanted.
   The room must be bright enough for auto exposure at or below ISO 800 at
   33 ms (roughly 30 lux on its surfaces, an estimate, not a measurement);
   the tool refuses a dimmer den as "too dark" before any hold.
3. **The owner starts `sweep.py` and steps out of view.** It checks
   `install.json`, counts down about 15 s and waits for a still scene, up to
   the still-scene wait limit.
4. **`sweep.py` holds and steps exposure** (still approval 1) for about one
   minute, inside den_lighting's 120 s vacancy window (the lights stay on for
   120 s after Frigate stops seeing a person); the run limit keeps it there.
   It writes no lights.

**Go or no-go.** Stage 0 passes if the residual is at most R in every
channel, the region test passes, and every held frame reports gain and white
balance and passes gain agreement. If it fails, the owner chooses RAW output
(a bigger camera change with its own approval) or stopping rung 8, before any
of Stage 1 is built.

**Void sweeps.** A void is a repeat mismatch, a still-scene wait over about
20 s, a 503 or an unreachable camera, a new camera session, a run longer
than about 100 s (the METHOD-V1 limits), or a sweep interrupted or errored
before the repeat (Ctrl+C, a refused `POST /meter`, or any other error before
the repeat's frames are in). It is neither a pass nor a fail.
`response.json` records outcome void and the reason, and the void's frames are
deleted. Up to 3 reruns the same evening fall under approval 1. A later rerun,
or one after a code or METHOD change, needs approval 1 again; the server is
reinstalled only if its code changed.

## Stage 1: the light model (only if Stage 0 passes)

Finish METHOD-V1, build the rest offline against a synthetic den and a fake
Home Assistant on 127.0.0.1, then run one probe session in the empty den after
dark: snapshot den_lighting's normal scene, pause it, hold the camera, step
the bulbs for about ten minutes, restore, resume. Reduce, fit, and score on
that session's 16 hold-outs.

| File | Runs on | Does |
|---|---|---|
| `METHOD-V1.md`, the rest | repo | The held-exposure rule, the probe schedule, the block grid and its rules, the reduction rule (the session mask, its union across captures and the minimum unmasked share per block; The model), every other tolerance (Stage 0's noise tolerance is reused), the noise floor and the gate. Committed before any probe. |
| `guard.py` | PC | Pure rules, no I/O: start checks, own-or-foreign decisions, the abort decision, the restore plan and its order. |
| `probe.py` | PC | Runs one session. Holds a small HA REST client and reuses `sweep.py`'s camera client. `--recover` finishes an interrupted session. |
| `model.py` | PC | Reduces frames to `readings.json`, builds the schedule, fits, predicts, scores; writes `light-model.json` and `score.json`. |
| `synthetic.py` | anywhere | The synthetic den, a fake camera and a fake HA. The fake HA runs den_lighting's `policy.py` unchanged, loaded by path as den-core's `incumbent.py` does, plus copies of `__init__.py`'s learning code (see Tests). |
| `README.md`, the rest | repo | Stage 1's owner steps. |

den-light does not import den-core and adds nothing to it. A change fragment
goes in `changes/unreleased/` with each stage's tools.

### What a session records

One folder per session, `C:\den-light\<session-id>\`, under the run-folder
rules above.

| File | Holds | Agents read it |
|---|---|---|
| `session.json` | Written before the pause, updated at each phase: id, times, code and METHOD sha256s, start readings, the normal-scene snapshot and its sha256, colour-temperature ranges, camera session, the held (t, G), outcome, abort reason, `unrestored`, and the restore, resume and walk-through checks. | yes |
| `probes.jsonl` | The run's log of non-human changes (`den-light-probe-1`), each line flushed as written: an intent line before every HA call (bulb, service, data, PC time), then HA's context id, marked own, after the response; each observed change of a bulb, the occupancy signals, the switch and the sensor, marked own or foreign; each capture; guard events and phases. | yes |
| `frames\` | The RGB NV12 plane and metadata of each frame used, about 0.35 MB each. No depth, left or right. | never |
| `readings.json` | From `model.py`: the session mask and each block's unmasked share; per step and block, the reading, its noise and its flags. | yes |

The room is empty, so frames should show no one. After an abort, frames after
the last clean step are deleted before `probe.py` exits, or by `--recover`
after a kill. Otherwise they may stay on the PC until the owner deletes them,
as the owner's 2026-11-02 answer allows for den footage on household
computers. Once `readings.json` exists, nothing needs them.

### The model

- b is a block of the fixed grid METHOD-V1 sets before any probe, for example
  20x20 px (32 by 18 blocks). v1 blocks have no kind, `xyz` or `normal`.
- c is a bulb, u its brightness command (1 to 254; Zigbee caps at 254), k its
  colour temperature in kelvin.
- A reading is a block's mean in `meter-norm/v1` units over the session
  mask's unmasked pixels.
- **The reduction rule** is Stage 1's, not part of `meter-norm/v1`; Stage 1's
  METHOD-V1 part fixes it and the minimum share before any probe. A pixel is
  masked if `meter-norm/v1` flags it in any capture the session fits or
  scores; the session mask is the union over those captures, fixed before
  reduction, so every reading, D included, averages the same pixels in every
  step. A block whose unmasked share is below the minimum (for example 50%),
  or that is missing from any basis capture, is excluded from fitting and from
  every hold-out pair, and counted in `score.json`.
- Each block's all-off noise sets its noise floor; a block whose all-off
  brackets disagree beyond tolerance has the steps between them flagged and
  unused.

**One held exposure.** Every fitted and scored capture uses one (t, G).
METHOD-V1 fixes the rule that picks it: at session start, with all four bulbs
at full and cool, the (t, G) that keeps the 98th percentile of Y below the
clipping limit. Blocks on the bulbs themselves may clip: their clipped pixels
are masked, and a block left below the minimum share is excluded.
Session 1 also captures one bulb at full at three of Stage 0's exposures, to
confirm the Stage 0 curve instead of re-sweeping.

**Basis.** B_c(b, k) is bulb c alone at full at temperature k, minus the
all-off reading, so it includes every bounce the camera sees (`bounce:
measured`). Daylight and stray light D(b, t) come from all-off captures that
bracket each run of steps, interpolated linearly in time. Each step value is
the median over its settled frames (consecutive frames agree within Stage 0's
noise tolerance).

**Dimming.** Each bulb steps through 1, 2, 4, 8, 16, 32, 64, 96, 128, 176 and
224, plus full, at the middle temperature. g_c(u) is the median over
signal-rich blocks (at least 20% of that bulb's brightest) of (reading − D) /
B_c(b, k_mid), stored as a table made monotone by pool-adjacent-violators,
with g(0) = 0 and g(254) = 1, linear in u between levels. If both bulbs of a
pair agree within 3% from level 16 up, they share one curve (the `WARMEST`
defaults, 1000 K front and 2202 K rear, suggest two types). The model assumes
g does not depend on temperature; there are no warm-end steps.

**Colour temperature.** Each bulb's full output is captured at the warm end,
the middle in mired, and the cool end of the range all four share (from
`min_color_temp_kelvin` and `max_color_temp_kelvin`). The model interpolates
piecewise-linearly in mired and clamps outside. Predicting the middle from the
ends tests A9's linear mixing; the hold-outs test uncaptured temperatures.

**Prediction.** Ê(b) = Σ_c g_c(u_c) · B_c(b, k_c), over the bulbs that are
on: the electric light in relative units, a few thousand multiply-adds, far
inside rung 9's 100 ms budget. The total is D(b) + Ê(b); rung 8 does not model
daylight.

**`light-model.json`** follows A9's fields: `version`; `room_version` (the
view and block grid from METHOD-V1, with its sha256); `meter_norm` and the
sha256 of its parameters; `sample_points`, one per block, `{point_id, kind:
null, xyz: null, normal: null, rect}`; `basis` per bulb and temperature with
`basis_unit: relative`; `dimming` with the shared-curve result; `cct`;
`bounce: measured`; `glare: null`; `error`, pooled as the schema defines
(median, p90, count, excluded count, verdict); and `sessions` with each
`readings.json` sha256.

### Scoring against the gate

Session 1 holds 16 mixed scenes (two or more bulbs on), none used in fitting:
den_lighting's normal scene from the snapshot; its gaming scene (all four at
38, each at its warmest); all four at full and middle temperature; all four at
full, two warm and two cool; and 12 seeded random scenes. In those, each bulb
is on with probability 0.7, at a level drawn log-uniform from 8 to 254 that is
not a fitted step, and a temperature uniform in mired that was not captured.
At least three are near full combined output.

For each hold-out and block, E is the reading minus D and the error is
|Ê − E| / E. Pairs with E below 5 times the block's all-off noise are excluded
and counted; the floor is fixed in METHOD-V1 before any probe.

**The gate passes when the pooled median is at most 8% and the pooled p90 at
most 19%**, percentiles by linear interpolation as in den-take's CHECK-V1.
Nothing else enters the verdict. `score.json` also reports "absolute: not run,
waits on photometry-commissioning", the camera-scale caveat, "day-to-day
stability: not tested at rung 8 (one session); left to rung 10 Verify and K21
drift checks", the count of blocks excluded by the mask minimum or a missing
basis, and, for information only: hold-out error by temperature band, each
step's SNR, drift within the session (one full-output capture at the start
and the end), the shared-curve result, the middle-temperature error and the
response confirmation. Per-kind figures come with point kinds in rung 9.

**One planned session.** The gate is scored on session 1's hold-outs. A second
session runs only if session 1 aborts, fails for a fixable reason, needs
per-temperature curves, or anything is tuned on its hold-outs.

### Probe safety with den_lighting

**What the code does** (den_lighting 1.0.4,
`ha-config/custom_components/den_lighting/` on `claude/den-core`):

- The normal scene lives only in the store, `.storage/den_lighting.state`
  (`Controller.dump()`: `normal`, `snapshot`, `holds`, flags). The status
  sensor "Den lighting control" does not include it.
- Pausing is turning off the switch "Den lighting automatic":
  `set_enabled(False)`, after which `reconcile()` sends nothing. Turning it on
  raises unless `automation.den_lights_follow_person_occupancy` is off.
- Learning does not pause. `service_called()` and `state_changed()` in
  `__init__.py` call `policy.manual()`, which stores the scene as a hold and,
  unless a game is on, as `normal`. A `light.turn_on` with brightness and
  `color_temp_kelvin` is learned exactly (`command_scene()`). Anything a call
  leaves out comes from `policy.observed`: during a session, the probe's last
  write. A report outside the call's window (transition plus 3 s) is learned
  from the state, and an off state has no level. A change to or from
  unavailable or unknown is not learned.
- `resume_automatic` clears the listed bulbs' holds. `DenRuntime.resume()`
  checks bulbs already at target only when automatic is on.
- After 120 s of vacancy with no hold, den_lighting turns bulbs off with its
  own context, which it does not learn.
- When occupancy goes straight from off to on after 120 s or more of vacancy,
  `normal` becomes on at each bulb's last level (`policy.py:102-104`), even
  while paused.
- With occupancy unknown, it sends nothing. Only a fresh Frigate person count
  (or its HA sensor turning on) ends unknown, and Frigate publishes only on
  change.

**The order.**

1. **Start: copy, then leave and wait.** In the den, the owner copies the
   store file to `C:\den-light\private\den_lighting.state` and starts
   `probe.py`. The tool finds exactly one switch and one sensor by name. It
   refuses unless the copy has `enabled` true, `gaming` false, empty `holds`
   and `snapshot`, and a `normal` scene for all four bulbs with no null
   brightness or kelvin (setting a bulb once in the HA app fills one). It
   also refuses unless the sensor's attributes (not its state word, which a
   stale gaming signal alone turns to `signal_unknown`) show `automatic`
   true, `error` null, `gaming_session` false and no pending or provisional
   fixtures, the incumbent automation is off, all bulbs are available and the
   camera is healthy. The snapshot goes
   into `session.json`. The tool tells the owner to leave, then polls the
   sensor about once a second for up to about 10 minutes, refusing if holds
   appear or a game starts. It goes on once the sensor's `occupancy` and
   `binary_sensor.den_oak_person_occupancy` have read off for at least 120 s
   and all four bulbs are off.
2. **Pause.** Turn the switch off; check `automatic` reads false.
3. **Probe.** Hold the camera. For each probe: flush the intent line, send
   `light.turn_on` or `light.turn_off` with `transition: 0`, log the context
   id from the states HA returns, capture once settled. Check the abort rules
   between every frame. End all off, with a last all-off capture, and release
   the hold.
4. **Restore, still paused.** For each bulb still the tool's, send
   `light.turn_on` at the snapshot's brightness and kelvin. If the snapshot is
   off, wait for the on report, then send `light.turn_off`. Check through the
   sensor's `holds` that each restored hold equals the snapshot (`on` exact,
   brightness within 1, mired within 2). A bulb that fails, for example
   because its off report came more than 3 s after the turn_off and was
   learned with no level, is restored once more, then listed as unrestored.
5. **Resume.** Switch on first, since `resume()` only checks bulbs when
   automatic is on, then `resume_automatic` for the restored bulbs. With the
   room vacant, den_lighting turns them off itself. Check `automatic`, holds
   and bulbs off; write the result.

**A bulb is the tool's** when its state matches the tool's latest intent for
it within `Scene.matches()` tolerance, or the intent before when the latest
got no response. A write HA applied without the tool hearing back is still the
tool's. `--recover` always rebuilds from `session.json` and `probes.jsonl`.

**People in the dark.** Frigate's RTSP stream shares the held exposure. In
all-off brackets and the lowest dimming levels after dark, an unlit person is
invisible to both the camera guard and Frigate. So the schedule never runs
more than about 10 s of all-off or level 1 to 4 steps without one bulb at full
in between, using captures already planned.

**Aborts** happen on:

- occupancy (sensor or binary sensor) anything but off, including unknown;
- a bulb change that is not the tool's; a moved `last_updated` with a foreign
  context, even if the value came back; any change to the switch, the
  incumbent automation or `gaming_session`. A bulb going unavailable or
  unknown also aborts, but does not make it person-owned;
- the camera guard: frames within a settled step disagree, all-off brackets
  disagree, a 503, a new camera session, 15 s without a frame, or a frame
  that fails Stage 0's gain agreement against the hold;
- HA unreachable, or Ctrl+C.

On abort, in order:

1. Send no more probes and release the camera hold (it also expires).
2. Mark each bulb with a foreign change as person-owned. It is never written
   again; den_lighting keeps its hold, and its normal was learned from the
   change, with anything the change left out taken from the probe's last
   write.
3. If HA is unreachable, stop. `probe.py --recover <session>` does the rest
   once HA answers, after re-reading states. den_lighting stays paused until
   then. `--recover` also reads HA history (`/api/history/period`) for the
   den occupancy signals (the sensor's `occupancy` and
   `binary_sensor.den_oak_person_occupancy`) from the last sample
   `probes.jsonl` logged to now. In step 4 it gives a snapshot-off bulb
   turn_on only if that history shows a direct off-to-on change at least
   120 s after vacancy began, with no unknown, unavailable or gap. Otherwise
   it uses on-then-off. History holds only state changes, so a **gap** is
   either of: a signal has no state at the window start (HA's answer holds no
   entry for it at or before the start), or HA restarted inside the window
   (either signal reads `unavailable`, or `/api/logbook` for the window shows
   a `homeassistant_start` event).
4. Restore each other bulb still the tool's, as in the order's step 4. A
   bulb that only went unavailable is restored too if it reports on or off
   again while the room is empty; the tool waits about 5 min for it after the
   others are restored, then lists it as unrestored. **If occupancy is on,**
   a snapshot-off bulb gets only `light.turn_on` at its level and kelvin, no
   turn_off, so den_lighting learns the scene its arrival sets
   (`policy.py:104`). That needs the arrival rule to have run (straight from
   off to on, 120 s or more after the vacancy began, which the start's wait
   ensures during probing, and step 3's history shows under `--recover`); its
   hold check then expects the snapshot scene turned on. Otherwise, for
   example on through unknown, it gets on-then-off.
5. Switch on, then `resume_automatic` for the restored bulbs only.
6. List each bulb not restored (person-owned, or still unavailable) in
   `session.json` as `unrestored: [entity, last probe scene]` and in the
   result line, e.g. "light.X was not restored: its normal now carries the
   probe's level and temperature. Set it once by hand in the HA app."
7. Delete frames after the last clean step; mark the session aborted. v1 does
   not fit or score aborted sessions.

**Occupancy unknown at resume.** den_lighting sends nothing until a fresh
Frigate count, so restored bulbs could stay lit all night in an empty den. The
tool says so plainly and asks the owner to walk through the den once. It waits
for occupancy to go on and then off, confirms all four bulbs are off about
120 s later, and writes the outcome to `session.json`. If no walk-through
comes within about 30 min, it records "bulbs left lit, occupancy unknown" in
`session.json` and the result line and exits; `probe.py --recover <session>`
can redo the check later. It never turns them off itself as a shortcut: a
tool `turn_off` is learned as normal off, and coming back from unknown skips
the arrival rule, so the next arrival would get a dark room.

**A game starting mid-run** stops `manual()` learning, and the game's end
writes back the normal saved at its start. The tool leaves den_lighting paused
and asks for `probe.py --recover` after the game.

**Limits.** A person can change a bulb in the fraction of a second between the
tool's re-read and its restore write. A hard kill leaves den_lighting paused
and the bulbs in a probe state; the camera returns to auto by its timer, and
`--recover` finishes the job.

### Keeping probes out of corrections

`probes.jsonl` is the run's log of non-human changes, with each command's HA
context id marked own. correction-capture, the local tool that reads HA
recorder exports, already counts no probe-session hold, because den_lighting
is paused (reason `outside_exposure`). One leak remains: its `--events` replay
writes probe holds as `app` corrections. One small change closes it, made when
the first export spanning a probe session is analysed: read each
`session.json`'s start and end, and mark holds inside that window
`probe_session`, neither counted nor written.

den-core is the other replay. Its planned recorder-lights adapter (the HA
recorder export in its README, emitting `light_observed` with context
own/person/unknown) would label probe and restore writes as person, by the
token's user id. den-core makes no correction from that label, but any future
attribution rule must exclude `session.json` windows. Also, den-core's
incumbent replay passes recorded person states only to `observe()`, never
`manual()`, so a replay spanning a probe session diverges from live
den_lighting, which learned every probe write as normal.

### Stage 1 tests

All offline, synthetic data, fakes on 127.0.0.1 only:

```bash
python -B -m unittest discover -s tools/den-light -v
```

**`model.py` and scoring**

- A synthetic den (known B_c(b, k), two bulb types, linear-in-mired mixing,
  drifting daylight, noise): the fit recovers B and g within 1% and the
  hold-outs pass. A saturating sum and a wrong response curve fail the gate;
  a temperature-dependent curve shows in the error by temperature band.
- The shared-curve check is true within a pair and false across pairs.
- The schedule is the same for the same seed, no hold-out repeats a fitted
  step, and no run of all-off or level 1 to 4 steps lasts more than about
  10 s without a bulb at full.
- In a noise-free synthetic den, 3% of a block's pixels clipping only in a
  bright hold-out gives that block zero error, since the mask removes them
  from every step. A block clipped below the minimum share in one basis
  capture is excluded from fitting and from every pair, and counted in
  `score.json`. Disagreeing brackets flag a block. Fit and score from
  `readings.json` alone match those from frames.
- Percentiles match hand-worked values, the floor excludes and counts low
  pairs, the verdict uses only the pooled median and p90, the absolute line
  reads "not run", and `score.json` carries the day-to-day stability line.

**`guard.py`**

- Each start failure refuses: a hold, a game or a null normal field in the
  copy; a wrong sensor attribute; the incumbent automation on; a hold or game
  during the wait; no 120 s of vacancy with all bulbs off within 10 minutes.
- A clean run calls exactly: pause, probes, restore, hold check, switch on,
  `resume_automatic`. A snapshot-off bulb restores as on, then off.
- Occupancy mid-run: no probe write after it; only the tool's bulbs restored
  and resumed. With occupancy on after an off-to-on arrival, a snapshot-off
  bulb gets only turn_on; after an arrival through unknown, on-then-off.
- A foreign change: that bulb is never written again and is listed in
  `unrestored` with the message.
- A bulb unavailable then back while the room is empty is restored; one
  still unavailable about 5 min after the others are restored is listed as
  unrestored.
- Occupancy unknown at resume: the walk-through request, on then off, bulbs
  off about 120 s later, the outcome recorded, no turn_off of the tool's own.
  With no walk-through in about 30 min: "bulbs left lit, occupancy unknown"
  recorded, the tool exits, and `--recover` redoes the check.
- `--recover`'s branch: history with a direct off-to-on change 120 s or more
  after vacancy began gives a snapshot-off bulb turn_on only; under 120 s, or
  through unknown, on-then-off. Each kind of gap also gives on-then-off, one
  test each: a signal with no entry at or before the window start, an
  `unavailable` state inside the window, and a `homeassistant_start` logbook
  event inside the window with otherwise clean history.
- A write HA applied with its response lost, once by timeout or Ctrl+C and
  once by a kill: the bulb counts as the tool's, `--recover` restores it, and
  `normal` equals the snapshot.
- HA lost: no writes, and `--recover` finishes. A game mid-run: den_lighting
  stays paused and the tool asks for `--recover` after the game.

**den_lighting end to end.** The fake HA runs `policy.py` unchanged plus
copies of `__init__.py`'s `command_scene()`, `service_called()`'s learning
loop and `state_changed()`'s light branch. The tests pin the sha256 of both
files, so a new den_lighting version fails loudly, and `probe.py` refuses an
unknown store version or sensor attribute set. After a full synthetic
session, `normal` equals the snapshot, `holds` is empty and the bulbs are off.
Every abort test asserts the final `normal` and `holds`, including an off
report more than 3 s after a turn_off and a foreign change from a physical
switch.

**`probe.py` end to end.** A fake camera renders the synthetic den under the
held settings. A full session runs, then reduce, fit and score pass. Each
abort leaves den_lighting resumed or recoverable, with no frames after the
abort.

Synthetic runs prove plumbing. They are never evidence for the gate.

## Deliberately left out

- Absolute lux and the k_L anchor (rung 7). Occupied hold-outs and the
  visibility term V_total = (V_direct·E_dir + E_ind)/B, which needs the basis
  split into direct and indirect. Eye positions and glare.
- Drawn sample points (`points.html`, `all-on.png`), the working, wall and
  focal kinds, and point `xyz` and `normal`: rung 9.
- A daylight model; live or continuous metering; rung 9's coordinator.
- RAW capture (Stage 0's fallback, by the owner's choice) and mono-camera
  metering.
- A dim exposure tier, a gain ladder and warm-end dimming steps.
- A den-core `probe_log` adapter: nothing in the gate reads it.
- Other lights, and any Frigate configuration change.
- A den_lighting change exposing `normal` in its status: it would remove the
  store copy but needs a deploy to the live integration.
- Fitting from aborted sessions, and probing while the incumbent automation
  drives the den (the start refuses then).
- Day-to-day stability: not tested at rung 8 (one session); left to rung 10
  Verify and K21 drift checks.

## Approvals needed

Each needs the owner's yes first. Nothing below has happened.

One-time:

1. **Install the metering camera server and run Stage 0's one-minute camera
   check**: after the fixture check, the meter server copied into a new
   sibling release directory and a systemd drop-in that points
   `oak-den.service` at it with its flags unchanged (the pinned script is not
   touched), one restart of `oak-den.service` in an empty, dark den (Frigate's
   den feed drops briefly, and the den lights follow that feed), then
   exposure, gain, white balance and focus held for about 1 min while the
   owner is out of view; Frigate sees held settings meanwhile. No pause. The
   tool writes no lights. If no bulb is on, the
   owner turns one on in the HA app, and den_lighting keeps that as its
   normal scene until it is changed again; set it back by hand afterwards if
   wanted. Covers up to 3 void reruns the same evening. Rollback: remove the
   drop-in, `daemon-reload` and one more restart, then the rollback check
   (the after-check again, now expecting the before state and a healthy
   camera without the meter keys).
2. **A Home Assistant long-lived token on the PC** for `probe.py` (Stage 1),
   in `C:\den-light\private\`, never printed.

Per session:

3. **"Run probe session N", or a named batch**, with three effects: the
   camera held for about 12 minutes (Frigate, perception-lab and anything
   else reading it see held settings); about 150 light changes on the four
   bulbs over about 10 minutes in an empty den after dark; and den_lighting
   paused, then restored (snapshot written back, switch on,
   `resume_automatic`). Planned: session 1 only.

No decision blocks rung 8. Photometry-commissioning is not needed for it.

## Risks

- **An unlit person is invisible** to the camera guard and Frigate in dark
  steps. The schedule rule bounds each stretch to about 10 s. Flashes may
  cause false person detections, which abort safely.
- **A stale or wrong store copy.** The wait refuses on holds or a game, and
  the restored-hold check catches the rest. A wrong normal is fixed by setting
  the bulbs once by hand.
- **den_lighting may change after 1.0.4.** The tests pin `policy.py` and
  `__init__.py`; `probe.py` checks the store version and sensor attributes.
- **Short flashes run cooler than steady light**, as LEDs dim while warming.
  The model describes short on-times; the start-and-end repeat reports drift.
- **Daylight and stray light.** Sessions run after dark; brackets flag the
  rest. A PC in the den keeps its screen off or on one still page.
- **8-bit frames at low levels.** At one exposure, levels 1 to 4 sit near the
  noise floor. Blocks average many pixels and frames, the floor excludes weak
  pairs, and `score.json` reports each step's SNR.
- **REST polling** at about 1 s can miss a sub-second flicker. The
  `last_updated` check and the camera guard cover it.
- **Flicker at short exposures.** Stage 0's shortest steps (1/16 of auto's
  pick) may catch bulb or mains flicker. The repeat check then voids the
  sweep, or the residual shows it.
- **Port 8090 has never been reached from the PC.** Stage 0 finds out;
  den-take's ssh tunnel is the fallback, and POST works through it.
- **Disk.** The tools refuse on low disk (0.3-0.5 GB per session; a sweep
  needs far less).

## Owner time and effort

| When | What | Owner time |
|---|---|---|
| Stage 0, once | Install the camera server by pasting commands: fixture check, meter directory and drop-in, after-check, `install.json` | about 15 min |
| Stage 0, once | Light the room, start `sweep.py`, step out, read the result | about 5 min |
| Stage 1, once | Create the HA token | about 3 min |
| Each session | Copy the store file, start `probe.py`, leave; keep the den empty; read the result, run `model.py` | about 20 min: 3 before, a 2-minute vacancy wait, 12 during, 2 after |

| Stage | Part | Build days |
|---|---|---|
| 0 | `METHOD-V1.md`, Stage 0 part | 0.5 |
| 0 | `oak-capture-meter.py` and its tests | 1 |
| 0 | `meter.py` (decode, response fit, global check) and tests | 1 |
| 0 | `sweep.py` and its fake-server tests | 0.5 |
| 0 | `README.md`, Stage 0 part, and change fragment | 0.25 |
| 1 | `METHOD-V1.md`, the rest | 0.25 |
| 1 | `model.py` (reduce, schedule, fit, predict, score) and tests | 1.25 |
| 1 | `guard.py`, the den_lighting fake, and tests | 1.5 |
| 1 | `probe.py`, `synthetic.py` and end-to-end tests | 1.5 |
| 1 | `README.md`, the rest, and change fragment | 0.25 |

Stage 0: about 3 build days, then about 20 minutes of owner time and one
minute of camera. Stage 1, only after Stage 0 passes: about 5 build days, then
about 20 minutes of owner time for one session.
