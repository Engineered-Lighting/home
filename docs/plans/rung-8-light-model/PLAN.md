# Rung 8 plan: the den light model

Status: plan only. Nothing here is built or run yet. No host, camera or light
has been touched to write it. Revised on 2026-11-04 after an independent
review.

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

## Stage 0: can the camera meter light?

The biggest risk is the camera, not the model. The ISP's output may not turn
back into linear light (local tone mapping, for example), or DepthAI on the
Jetson may not hold white balance or report gain per frame. Stage 0 finds out
before any of Stage 1 is built.

**The installed camera.** The den's OAK-D Pro is served by `oak-den.service`
on DepthAI 3.10.0. Its server as installed is copied at
`tools/fixtures/installed-oak-capture-20261016.py`, sha256
`d052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132`. The RGB
camera runs automatic exposure, white balance and (if fitted) autofocus with
no control input, and each frame reports only `exposure_duration_seconds`.
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
| `METHOD-V1.md`, Stage 0 part | repo | `meter-norm/v1` and the response check in one fenced block the code reads: the fixed white balance, the clipping limits, the response residual tolerance and the gain-agreement tolerance (how closely each frame's reported exposure, gain and white balance must match the hold). Committed before any sweep frame. Every output records its sha256, as rung R's CHECK-V1 does. |
| `oak-capture-meter.py` | Jetson | The installed server plus the metering hold, and nothing else. |
| `meter.py` | PC | Pure: reads bundles (zipfile and a small .npy reader, as den-take does), decodes NV12, applies `meter-norm/v1`, fits the inverse response, runs the global check. |
| `sweep.py` | PC | The Stage 0 runner: holds the camera through `POST /meter` with den-take's camera token, steps exposure, releases, writes `response.json`. Never calls HA; writes no lights. |
| `test_den_light.py` | anywhere | All tests, synthetic data only. |

**`meter-norm/v1`** (the normalisation version A9 and E7 ask for): decode
NV12 as BT.601 limited range, as the server does for its JPEG; apply the
measured inverse response per channel; drop pixels within the clipping limits
per channel; divide by t·G (exposure in ms times ISO/100, from the frame's own
metadata); combine R, G and B with fixed weights. White balance is held at one
fixed value in every capture and is part of the version. The black level
cancels, because each contribution is a frame minus an all-off frame at the
same exposure. A rule change means a new version and a refit. Rung 7 later
reuses it for its camera scale.

**The metering hold** in `oak-capture-meter.py`:

- An authenticated `POST /meter` sets manual exposure and ISO, the METHOD
  white balance, and the current lens position if the module focuses, with a
  time to live of at most 120 s. The PC asks for 30 s and renews every 10 s.
  POST always needs the token, even on loopback.
- `POST /meter/release`, expiry and a server restart return the camera to
  exactly three modes: auto exposure on, white balance AUTO, and focus
  CONTINUOUS_VIDEO (not `AutoFocusMode.AUTO`, which focuses once and stops).
  A crashed PC tool can never leave the camera held.
- Each RGB frame adds the gain, white balance and lens position it reports,
  plus `meter: {held, generation}`. The schema stays 1, keys added only;
  den-take's `parse_metadata()` ignores extra keys.
- Nothing else changes. A test checks that the file equals the installed
  fixture outside its marked meter blocks, and perception-lab's bundle tests,
  which load the fixture, also run against it.

**The response fit and the global check.** `meter.py` fits a monotone inverse
response per channel: alternating least squares on a piecewise-linear curve,
made monotone with pool-adjacent-violators. One curve must fit the whole
image within the METHOD residual, with no pattern across image regions.

**`sweep.py`** reads auto-exposure's pick (t, G) from a few auto frames, then
holds and steps t·G from 1/16 to 2 times that pick in steps of about 1.41x
(11 exposures). It changes time, not gain, unless time would pass the 30 fps
frame time. It repeats the first exposure at the end; if the two disagree
beyond the METHOD noise tolerance, the scene moved and the sweep is void. It
releases on success, error and Ctrl+C. Its folder, `C:\den-light\stage0-<id>\`,
follows the session folder rules in Stage 1.

RAW output is the fallback: a bigger change with more USB load, and the server
ends its session if its RTSP writer falls about 2 s behind. Locking
auto-exposure where it is, the mono cameras and a second camera process were
rejected (less control; no colour and near-infrared; the server is the one OAK
owner).

**Stage 0 tests:**

- NV12 decode round-trips; the response fit recovers a known S-shaped curve
  within 1%; a synthetic local tone map fails the global check; clipped
  pixels are dropped; a frame whose gain or white balance misses the hold
  fails the gain-agreement check.
- `sweep.py` against a fake camera server: the exposure list, renewal,
  release on success, error and Ctrl+C, a moved scene voiding the sweep, and
  no HA call.
- The camera server against a fake control queue: request checks, the token
  rule, renewal, expiry and release. Release, expiry and restart set exactly
  auto exposure on, white balance AUTO and focus CONTINUOUS_VIDEO. The file
  matches the fixture outside its meter blocks.

### Run (approvals 1 and 2)

1. **Install the server** (approval 1). Preferred: stage it as a pilot release
   and switch with the existing `switch-pilot-release.sh` check, switch and
   rollback, which already checks the SHA256. That helper is not in this
   repo, so the build confirms its arguments first. By hand instead:
   `sha256sum` the script `systemctl cat oak-den.service` names and compare it
   with the fixture's sha256 above. If they differ, stop and change nothing.
   If they match, back it up, copy in the new file, and restart
   `oak-den.service` while the den is empty and dark. Frigate's den feed drops
   briefly; nothing should change. Check `/health` and that bundles show
   `held: false`. Rollback: the helper's, or the backup and one more restart.
2. **Light the room** with den_lighting's arrival scene, at least one bulb on.
   If none is on, the owner turns one on in the HA app. den_lighting stores
   that as the bulb's new normal (`policy.py` `manual()`).
3. **The owner starts `sweep.py` and steps out of view.** It counts down about
   15 s and waits for a still scene.
4. **`sweep.py` holds and steps exposure** (approval 2) for about one minute,
   inside den_lighting's 120 s vacancy window (the lights stay on for 120 s
   after Frigate stops seeing a person). It writes no lights.

**Go or no-go.** Stage 0 passes if the global check passes and the frames
report gain and white balance under the hold, within the gain-agreement
tolerance. If it fails, the owner chooses RAW output (a bigger camera change
with its own approval) or stopping rung 8, before any of Stage 1 is built.

## Stage 1: the light model (only if Stage 0 passes)

Finish METHOD-V1, build the rest offline against a synthetic den and a fake
Home Assistant on 127.0.0.1, then run one probe session in the empty den after
dark: snapshot den_lighting's normal scene, pause it, hold the camera, step
the bulbs for about ten minutes, restore, resume. Reduce, fit, and score on
that session's 16 hold-outs.

| File | Runs on | Does |
|---|---|---|
| `METHOD-V1.md`, the rest | repo | The held-exposure rule, the probe schedule, the block grid and its rules, every tolerance, the noise floor and the gate. Committed before any probe. |
| `guard.py` | PC | Pure rules, no I/O: start checks, own-or-foreign decisions, the abort decision, the restore plan and its order. |
| `probe.py` | PC | Runs one session. Holds a small HA REST client and reuses `sweep.py`'s camera client. `--recover` finishes an interrupted session. |
| `model.py` | PC | Reduces frames to `readings.json`, builds the schedule, fits, predicts, scores; writes `light-model.json` and `score.json`. |
| `synthetic.py` | anywhere | The synthetic den, a fake camera and a fake HA. The fake HA runs den_lighting's `policy.py` unchanged, loaded by path as den-core's `incumbent.py` does, plus copies of `__init__.py`'s learning code (see Tests). |
| `README.md` | repo | Owner steps and privacy rules. |

den-light does not import den-core and adds nothing to it. A change fragment
goes in `changes/unreleased/` with each stage's tools.

### What a session records

One folder per session, `C:\den-light\<session-id>\`. Like den-take, the
tools refuse a folder inside a git checkout or OneDrive, one that already
holds a session, and a PC low on disk. The HA token lives in
`C:\den-light\private\`; no code path prints or writes either token.

| File | Holds | Agents read it |
|---|---|---|
| `session.json` | Written before the pause, updated at each phase: id, times, code and METHOD sha256s, start readings, the normal-scene snapshot and its sha256, colour-temperature ranges, camera session, the held (t, G), outcome, abort reason, `unrestored`, and the restore, resume and walk-through checks. | yes |
| `probes.jsonl` | The run's log of non-human changes (`den-light-probe-1`), each line flushed as written: an intent line before every HA call (bulb, service, data, PC time), then HA's context id, marked own, after the response; each observed change of a bulb, the occupancy signals, the switch and the sensor, marked own or foreign; each capture; guard events and phases. | yes |
| `frames\` | The RGB NV12 plane and metadata of each frame used, about 0.35 MB each. No depth, left or right. | never |
| `readings.json` | From `model.py`: per step and block, the reading, its noise and its flags. | yes |

Frames are never copied to git, OneDrive or the cloud. The room is empty, so
they should show no one. After an abort, frames after the last clean step are
deleted before `probe.py` exits, or by `--recover` after a kill. Otherwise
they may stay on the PC until the owner deletes them, as the owner's
2026-11-02 answer allows for den footage on household computers. Once
`readings.json` exists, nothing needs them.

### The model

- b is a block of the fixed grid METHOD-V1 sets before any probe, for example
  20x20 px (32 by 18 blocks). v1 blocks have no kind, `xyz` or `normal`.
- c is a bulb, u its brightness command (1 to 254; Zigbee caps at 254), k its
  colour temperature in kelvin.
- A reading is a block's mean in `meter-norm/v1` units over its unclipped
  pixels. Per METHOD-V1, a block with too many clipped pixels in a step is
  dropped from that step and counted; each block's all-off noise sets its
  noise floor; a block whose all-off brackets disagree beyond tolerance has
  the steps between them flagged and unused.

**One held exposure.** Every fitted and scored capture uses one (t, G).
METHOD-V1 fixes the rule that picks it: at session start, with all four bulbs
at full and cool, the (t, G) that keeps the 98th percentile of Y below the
clipping limit. Blocks on the bulbs themselves may clip and are dropped.
Session 1 also captures one bulb at full at three of Stage 0's exposures, to
confirm the Stage 0 curve instead of re-sweeping.

**Basis.** B_c(b, k) is bulb c alone at full at temperature k, minus the
all-off reading, so it includes every bounce the camera sees (`bounce:
measured`). Daylight and stray light D(b, t) come from all-off captures that
bracket each run of steps, interpolated linearly in time. Each step value is
the median over its settled frames (consecutive frames agree within the
METHOD noise tolerance).

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
waits on photometry-commissioning", the camera-scale caveat, and, for
information only: hold-out error by temperature band, each step's SNR, drift
within the session (one full-output capture at the start and the end), the
shared-curve result, the middle-temperature error and the response
confirmation. Per-kind figures come with point kinds in rung 9.

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
  disagree, a 503, a new camera session, 15 s without a frame, or frames whose
  exposure, gain or white balance miss the hold;
- HA unreachable, or Ctrl+C.

On abort, in order:

1. Send no more probes and release the camera hold (it also expires).
2. Mark each bulb with a foreign change as person-owned. It is never written
   again; den_lighting keeps its hold, and its normal was learned from the
   change, with anything the change left out taken from the probe's last
   write.
3. If HA is unreachable, stop. `probe.py --recover <session>` does the rest
   once HA answers, after re-reading states. den_lighting stays paused until
   then.
4. Restore each other bulb still the tool's, as in the order's step 4. A
   bulb that only went unavailable is restored too if it reports on or off
   again while the room is empty. **If occupancy is on,** a snapshot-off bulb
   gets only `light.turn_on` at its level and kelvin, no turn_off, so
   den_lighting learns the scene its arrival sets (`policy.py:104`). That
   needs the arrival rule to have run (straight from off to on, 120 s or more
   after the vacancy began, which the start's wait ensures during probing);
   its hold check then expects the snapshot scene turned on. Otherwise, for
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
120 s later, and writes the outcome to `session.json`. It never turns them off
itself as a shortcut: a tool `turn_off` is learned as normal off, and coming
back from unknown skips the arrival rule, so the next arrival would get a dark
room.

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
- Clipped blocks are dropped and disagreeing brackets flag a block. Fit and
  score from `readings.json` alone match those from frames.
- Percentiles match hand-worked values, the floor excludes and counts low
  pairs, the verdict uses only the pooled median and p90, and the absolute
  line reads "not run".

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
  still unavailable at the end is listed as unrestored.
- Occupancy unknown at resume: the walk-through request, on then off, bulbs
  off about 120 s later, the outcome recorded, no turn_off of the tool's own.
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

## Approvals needed

Each needs the owner's yes first. Nothing below has happened.

One-time:

1. **Install the metering camera server** (Stage 0): one restart of
   `oak-den.service` in an empty, dark den. Frigate's den feed drops briefly,
   and the den lights follow that feed. Rollback: the pilot helper's, or the
   backup and one more restart.
2. **Stage 0's camera check**: exposure, gain, white balance and focus held
   for about one minute while the owner is out of view. No light changes, no
   pause; Frigate sees held settings meanwhile.
3. **A Home Assistant long-lived token on the PC** for `probe.py` (Stage 1),
   in `C:\den-light\private\`, never printed.

Per session:

4. **"Run probe session N", or a named batch**, with three effects: the
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
- **Port 8090 has never been reached from the PC.** Stage 0 finds out;
  den-take's ssh tunnel is the fallback, and POST works through it.
- **Disk.** `probe.py` refuses on low disk (0.3-0.5 GB per session).

## Owner time and effort

| When | What | Owner time |
|---|---|---|
| Stage 0, once | Install the camera server by pasting commands | about 15 min |
| Stage 0, once | Light the room, start `sweep.py`, step out, read the result | about 5 min |
| Stage 1, once | Create the HA token | about 3 min |
| Each session | Copy the store file, start `probe.py`, leave; keep the den empty; read the result, run `model.py` | about 20 min: 3 before, a 2-minute vacancy wait, 12 during, 2 after |

| Stage | Part | Build days |
|---|---|---|
| 0 | `METHOD-V1.md`, Stage 0 part | 0.5 |
| 0 | `oak-capture-meter.py` and its tests | 1 |
| 0 | `meter.py` (decode, response fit, global check) and tests | 1 |
| 0 | `sweep.py` and its fake-server test | 0.5 |
| 1 | `METHOD-V1.md`, the rest | 0.25 |
| 1 | `model.py` (reduce, schedule, fit, predict, score) and tests | 1.25 |
| 1 | `guard.py`, the den_lighting fake, and tests | 1.5 |
| 1 | `probe.py`, `synthetic.py` and end-to-end tests | 1.5 |
| 1 | `README.md` and change fragments | 0.5 |

Stage 0: about 3 build days, then about 20 minutes of owner time and one
minute of camera. Stage 1, only after Stage 0 passes: about 5 build days, then
about 20 minutes of owner time for one session.
