# Rung 8 plan: the den light model

Status: plan only. Nothing here is built or run yet. No host, camera or light
has been touched to write it.

## Context

Rung 8 is the light track's model. It predicts what any scene of the den's four
Hue bulbs does at chosen points in the room. It replaces aim math, beam traces,
hand-drawn footprints, and the virtual den's one-bounce sum with assumed
photometry. Rung 9's coordinator needs it to predict before it acts.

The bulbs are den_lighting's `LIGHTS`: `light.den_front_left`,
`light.front_right`, `light.den_rear_left` and `light.den_rear_right`.

What the rung asks for:

- Fit a per-channel basis from camera captures in the empty den.
- Test on held-out mixed scenes the basis was not fitted on.
- Gate: relative error against camera estimates, median 8% and p90 19%.
- Use rung 7's probe rule: den_lighting paused, probes logged as non-human
  changes and kept out of corrections, normal scene written back before
  `den_lighting.resume_automatic`, abort on occupancy or on any change the run
  did not send.

The basis and the scores come from the same camera. So the camera's scale
(k_L) and surface reflectance (ρ) cancel. The score is relative. It does not
wait on rung 7's absolute anchor, and it cannot detect a camera-scale error.
Absolute lux checks wait on the photometry-commissioning decision and are left
out.

This plan covers the empty-room relative model. The occupied visibility term
is a later step.

What exists today:

- den_lighting 1.0.4 is the den's live integration. Its code is tracked on
  `claude/den-core` under `ha-config/custom_components/den_lighting/`.
- `tools/den-core` on the same branch is the offline replay spine. Its line
  format is den-event-1 (`events.py`).
- The den camera is an OAK-D Pro on the Jetson, served by `oak-den.service`.
  A copy of its server as installed is
  `tools/fixtures/installed-oak-capture-20261016.py`.
  It runs RGB auto-exposure and has no exposure, gain or white-balance control.
- Rung R's `tools/den-take` (branch `claude/rung-r`) records 640x360 bundles
  on the PC by polling `/frame.npz`, without changing the camera.
- No camera light measurement runs today.

## Approach

1. Write the method first. `METHOD-V1.md` fixes `meter-norm/v1`, the probe
   schedule, every tolerance, the noise floor and the gate before any probe.
   This copies rung R's CHECK-V1 pattern: the code reads its numbers from one
   fenced block, and every output records the file's sha256.
2. Build everything offline against a synthetic den: fake bulbs, a fake camera
   response, a fake camera server and a fake Home Assistant on 127.0.0.1.
3. Add one small, opt-in metering hold to the camera server. It holds exposure,
   gain, white balance and, if the module focuses, focus, only while the PC
   keeps renewing it. It is installed once, with the owner's approval.
4. Run probe sessions in the empty den, after dark, from the PC. Each session:
   snapshot den_lighting's normal scene, pause den_lighting, hold the camera,
   step the bulbs, restore, resume. About ten minutes of probing.
5. The owner marks sample points once on a picture of the lit room.
6. Fit the model and score it on held-out mixed scenes from the same session.
   A second, shorter session on another evening repeats the hold-outs only.

Session 1 also measures the camera's response curve. If the ISP output cannot
be linearised (see Risks), rung 8 stops there and says so.

## Files

All new files live in `tools/den-light/`. They are standard-library Python
3.12 unless stated. The PC is Windows with PowerShell 5.1.

| File | Runs on | Does |
|---|---|---|
| `METHOD-V1.md` | repo | `meter-norm/v1`, the probe schedule, all tolerances, the noise floor and the gate, in one fenced block the code reads. Committed before any probe. |
| `probe.py` | PC | Runs one probe session: preflight, snapshot, pause, camera hold, probes, restore, resume, aborts. Holds the small HA REST and camera clients. `--recover` finishes an interrupted session. |
| `guard.py` | PC | Pure rules, no I/O: preflight checks, the snapshot, foreign-change detection, the abort decision, the restore plan and its order. |
| `meter.py` | PC | Pure: reads bundles (zipfile and a small .npy reader, as den-take does), decodes NV12, applies `meter-norm/v1`, fits and checks the inverse response. |
| `model.py` | PC | Reduces a session's frames to `readings.json`, builds the probe schedule, fits the basis, dimming curves and colour temperature, predicts any scene, scores the hold-outs, writes `light-model.json` and `score.json`. |
| `points.html` | PC browser | The owner drags rectangles on `all-on.png`, tags each as working, wall or focal, and saves `sample-points.json`. Works offline. |
| `synthetic.py` | anywhere | The synthetic den, a fake camera server and a fake HA on 127.0.0.1. The fake HA runs den_lighting's `policy.py` unchanged, loaded by path as den-core's `incumbent.py` does. |
| `test_den_light.py` | anywhere | All tests, synthetic data only. |
| `README.md` | repo | Owner steps and privacy rules. |
| `oak-capture-meter.py` | Jetson | The installed camera server plus the metering hold, and nothing else. |
| den-core `sources.py`, `events.py`, `run.py`, `test_spine.py`, `README.md` | PC | A `probe_log` adapter: probe sessions enter den-event-1 as `light_observed` with context `probe`, never as `correction`. |

den-light does not import den-core. The adapter is added to den-core on
`claude/den-core`, or on `main` once that branch merges. A change fragment
goes in `changes/unreleased/` when the tools are built.

## What a probe session records

Each session writes one folder on the PC: `C:\den-light\<session-id>\`. Like
den-take, `probe.py` refuses a folder inside a git checkout or OneDrive, a
folder that already holds a session, and a low-disk PC. The HA token lives in
`C:\den-light\private\`. The camera token is den-take's existing file,
`C:\den-takes\private\camera-token.txt`. No code path prints or writes either
token.

| File | Holds | Agents read it |
|---|---|---|
| `session.json` | Session id, times, code and METHOD sha256s, preflight readings, the normal-scene snapshot and its sha256, each bulb's colour-temperature range, camera session id, hold settings per tier, outcome and abort reason, restore and resume checks. | yes |
| `probes.jsonl` | One line per event, format `den-light-probe-1`: each command (bulb, service, data, HA context id, PC time); each observed change of a bulb, the occupancy signals, the den_lighting switch and status sensor, marked own or foreign; each capture (step id, scene, tier, frame files, frames dropped while settling); guard events; phase changes. | yes |
| `frames\` | The RGB NV12 plane and metadata of each frame used. No depth, left or right planes. About 0.35 MB per frame, 0.3 to 0.5 GB per session. | never |
| `all-on.png` | A small linearised picture of the lit room, for `points.html`. | never |
| `response.json` | Session 1 only: the fitted inverse response and its checks. | yes |
| `readings.json` | Written later by `model.py`: per step and sample point, the normalised reading and its noise. | yes |

Rules for the frames:

- They never leave the PC. Raw frames are never copied to git, OneDrive or
  the cloud.
- The room is empty, so frames should show no one. If a session aborts, every
  frame after its last clean step is deleted before `probe.py` exits.
- By default frames stay on the PC until the owner deletes them. This keeps
  re-reduction possible if sample points change. See Approvals.

## The model

Notation:

- p is a sample point: an owner-drawn rectangle in the 640x360 image, with a
  kind (working, wall or focal). `xyz` and `normal` stay null in v1.
- c is a bulb. u is its brightness command in den_lighting's units (1 to 254;
  Zigbee caps at 254). k is its colour-temperature command in kelvin.
- A reading is a sample point's mean in `meter-norm/v1` counts.

### Readings under meter-norm/v1

`meter-norm/v1` is the normalisation version that A9 and E7 ask for:

1. Decode NV12 to R'G'B' as BT.601 limited range, the same reading the
   server uses for its JPEG.
2. Apply the measured inverse response per channel to get linear values.
3. Drop clipped pixels (near either end of the code range) per channel.
4. Divide by t·G: exposure time in ms times analogue gain (ISO/100), both read
   from the frame's own metadata.
5. Combine R, G and B with fixed weights.
6. White balance is held at one fixed value for every session. It is part of
   the version, so the channel gains never change under it.

The black level cancels in every difference, because each contribution is a
frame minus an all-off frame at the same exposure and gain. The inverse
response comes from an exposure sweep in session 1 (see Camera exposure). A
rule change means a new version and a refit.

Rung 7 later reuses `meter-norm/v1` for its camera scale, so its anchor can
apply to this model without refitting.

### Basis

B_c(p, k) is bulb c alone at full output (u = 254) at temperature k. It is the
full-output reading minus the all-off reading, so it includes direct light
plus every bounce the camera sees (`bounce: measured`).

Daylight and stray light D(p, t) come from all-off captures that bracket each
block of steps. D is interpolated linearly in time between brackets. If two
brackets differ by more than the METHOD tolerance at a point, the steps
between them are flagged and not used. Sessions run after dark to keep D small
and steady.

Each step value is the median over its settled frames. A step settles when
consecutive frames agree within the METHOD noise tolerance.

### Dimming curves

Each bulb steps through 11 levels at the middle temperature: 1, 2, 4, 8, 16,
32, 64, 96, 128, 176 and 224, plus full (254).

- For each level, g_c(u) is the median over signal-rich points of
  (reading minus D) divided by B_c(p, k_mid). Signal-rich means at least 20% of
  that bulb's brightest point.
- g_c is stored as a table, made monotone by pool-adjacent-violators, with
  g(0) = 0 and g(254) = 1. Between levels it is interpolated linearly in u.
- Low levels are also captured at the dim exposure tier (see Camera exposure)
  when their bright-tier signal is below the METHOD threshold.

Two checks, both reported:

- **Shared curve.** den_lighting's `WARMEST` defaults are 1000 K for the front
  pair and 2202 K for the rear pair, which suggests two bulb types. If both
  bulbs of a pair agree within 3% at every level from 16 up, they share one
  stored curve. Otherwise each bulb keeps its own. A shared curve lets later
  sessions step one bulb per type.
- **Temperature independence.** One bulb per pair steps through four levels
  at its warm end. If g differs from the middle-temperature curve by more than
  3%, the model stores curves per temperature and the next session captures
  them.

### Colour temperature

Each bulb's full output is captured at three temperatures: the warm end, the
middle in mired, and the cool end of the range all four bulbs share. Each
range comes from the bulb's `min_color_temp_kelvin` and `max_color_temp_kelvin`
attributes, read at preflight. den_lighting already reads the first one.

A9 says colour mixes linearly. The plan tests it: the middle capture is
predicted from the two ends by interpolating in mired, and the error is
reported. The model always interpolates piecewise-linearly in mired across all
three captures, and clamps outside them. The hold-outs then test it at
temperatures that were never captured.

### Prediction

For a scene {(u_c, k_c)} with off bulbs left out:

Ê(p) = Σ_c g_c(u_c) · B_c(p, k_c)

This is the electric light at p, in relative units. The total is D(p) + Ê(p),
where D is measured at session time. Rung 8 does not model daylight.

A prediction for all sample points is a few hundred multiply-adds, far inside
rung 9's 100 ms decision budget.

### The LightModel record

`light-model.json` follows A9's fields:

- `version` and `room_version` (a `view_id` from `sample-points.json`, plus
  its sha256).
- `meter_norm`: `meter-norm/v1` and the sha256 of its parameters.
- `sample_points`: `{point_id, kind, xyz: null, normal: null, rect}`.
- `basis` per bulb and captured temperature; `basis_unit: relative`.
- `dimming`: tables per bulb or per type, with the check results.
- `cct`: the captured temperatures per bulb and the interpolation rule.
- `bounce: measured`. `glare: null` (out of scope).
- `error`: per kind and pooled, median, p90, count and excluded count, plus
  the gate verdict (next section).
- `sessions`: ids and the sha256 of each `readings.json` used.

## Scoring against the gate

Hold-out scenes are mixed: two or more bulbs on. None is used in fitting.
Session 1 holds 16:

- den_lighting's own normal scene, from the snapshot.
- den_lighting's gaming scene: all four at brightness 38, each at its warmest.
- All four at full, middle temperature.
- All four at full, two warm and two cool. This and the previous scene cover
  "near full combined output".
- 12 seeded random scenes. Each bulb is on with probability 0.7, at a level
  drawn log-uniform from 8 to 254 that is not a fitted step, and at a
  temperature drawn uniformly in mired that was not captured. At least three
  of them are near full combined output.

For each hold-out scene and sample point:

- E is the measured electric light: the reading minus D.
- Ê is the prediction.
- The error is |Ê − E| / E.
- Pairs where E is below 5 times that point's all-off noise are excluded and
  counted. The floor is fixed in METHOD-V1 before any probe, so it cannot be
  tuned to pass.

Per kind (working, wall, focal) and pooled: median and p90, with percentiles
by linear interpolation as in den-take's CHECK-V1.

The gate passes when every kind with at least 30 pairs, and the pooled set,
has median ≤ 8% and p90 ≤ 19%. A kind with fewer than 30 pairs is reported as
"not enough points" and fails the gate. The owner can add points and re-reduce
from the kept frames without a new session.

`score.json` reports separately:

- **Relative qualification:** pass or fail, with every number above.
- **Absolute qualification:** "not run: waits on photometry-commissioning".
- The caveat that this score cannot detect a camera-scale error.
- Session checks: repeatability (one full-output capture at the start and the
  end), the shared-curve and temperature checks, and the response residual.

Session 2, on another evening, repeats the 16 hold-outs and two basis
captures. It scores the session 1 model against them, as a day-to-day check.

## Probe safety with den_lighting

### What the code does

- den_lighting keeps its normal scene only in its store. `Controller.dump()`
  saves `normal`, `snapshot`, `holds` and the flags to HA's
  `.storage/den_lighting.state` (`Store(hass, 1, DOMAIN + ".state")`). The
  status sensor's attributes (`DenRuntime.status()`) do not include `normal`.
- Pausing means turning off the switch named "Den lighting automatic" (unique
  id `victoria_den_lighting_automatic`). That calls `set_enabled(False)`, and
  `Controller.reconcile()` then returns no commands.
- Learning does not pause. `state_changed()` and `service_called()` still call
  `policy.manual()`, and `manual()` overwrites `normal` unless a game is on.
  So every probe write is learned as a hold and as the normal scene.
- An ordinary `light.turn_on` with brightness and `color_temp_kelvin` is
  learned exactly: `command_scene()` builds the target from the call, and
  `manual()` stores it as both the hold and `normal`.
- Turning the switch on calls `set_enabled(True)`. It raises unless
  `automation.den_lights_follow_person_occupancy` is off.
- `den_lighting.resume_automatic` takes an optional list of the four bulbs.
  It clears their holds. `DenRuntime.resume()` checks bulbs already at their
  target only when automatic is on.
- With occupancy off for at least `VACANCY_SECONDS` (120 s) and no hold, the
  desired scene is off. den_lighting turns bulbs off with its own context,
  which it does not learn.

### The order

**1. Preflight (read only).** `probe.py` refuses to start unless all of this
holds:

- The status sensor "Den lighting control" (unique id
  `victoria_den_lighting_control`) has these attributes: `automatic` true,
  `error` null, `occupancy` `off`, `holds` empty, `gaming_session` false, and
  `pending_fixtures` and `provisional_fixtures` empty. The tool checks the
  attributes, not the sensor's state word, because a stale gaming signal
  alone turns that word into `signal_unknown`.
- `binary_sensor.den_oak_person_occupancy` is off.
- `automation.den_lights_follow_person_occupancy` is off.
- All four bulbs are available and off.
- The camera's `/health` is healthy.

The switch and sensor are found by name in `/api/states`. The tool stops if
there is not exactly one of each.

**2. Snapshot.** The tool asks the owner to copy den_lighting's store file to
`C:\den-light\private\den_lighting.state`, by their usual route into HA's
config folder. It reads the sensor's `revision` before and after the copy, and
asks again if it moved. It refuses unless the copy has `enabled` true, `gaming`
false, empty `holds` and `snapshot`, and a `normal` scene for all four bulbs
with no null brightness or kelvin. If any is null, setting that bulb's
brightness and colour temperature once in the HA app, while in the room,
gives den_lighting a full normal scene. The snapshot
and its sha256 go into `session.json`.

**3. Pause.** Turn off "Den lighting automatic". Check that the sensor's
`automatic` attribute reads false.

**4. Probe.** Hold the camera. Send each probe as `light.turn_on` or
`light.turn_off` with `transition: 0`. Record each call's context id from the
states HA returns. Capture once settled. Check the abort rules between every
frame. End with all four off, a last all-off capture, and the camera hold
released.

**5. Restore (still paused).** For each bulb, re-read its state first. If it
still shows the tool's last write, send `light.turn_on` with the snapshot's
brightness and kelvin. If the snapshot's `on` is false, wait for the bulb to
report on, then send `light.turn_off`. den_lighting learns the scene exactly.
Then check, through the sensor's `holds` attribute, that each restored hold
equals the snapshot: `on` exact, brightness within 1, mired within 2.

**6. Resume.** Turn "Den lighting automatic" on first, because
`DenRuntime.resume()` only checks bulbs when automatic is on. Then call
`den_lighting.resume_automatic` for the restored bulbs: all four in a clean
run. With the room vacant for 120 s or more, den_lighting turns the bulbs off
by itself. Check that `automatic` is true, `holds` is empty and the bulbs
are off. Write the result to `session.json`.

### Aborts

A session aborts on any of these:

- **Occupancy gate.** The sensor's `occupancy` attribute or
  `binary_sensor.den_oak_person_occupancy` is anything but `off`, including
  unknown.
- **A change it did not send.** A bulb state whose context is not one of the
  tool's, and which does not match its last command within den_lighting's
  `Scene.matches()` tolerance. Also a moved `last_updated` with a foreign
  context, even if the value came back. Also any change to the switch, the
  incumbent automation, or the sensor's `gaming_session`.
- **The camera guard.** Frames within a settled step disagree, or two all-off
  brackets disagree beyond tolerance. This backs up Frigate while exposure is
  held. Also a camera 503, a new camera session, 15 s with no new frame, or
  frames whose exposure, gain or white balance do not match the hold.
- **HA unreachable**, or Ctrl+C.

On abort, in this order:

1. Send no more probes. Release the camera hold. The hold also expires by
   itself.
2. Mark every bulb with a foreign change as person-owned. It is never written
   again. den_lighting keeps its hold of the person's change, as it does for
   any person's change.
3. If HA is unreachable, write `recover.json` and stop.
   `probe.py --recover <session>` does the next two steps once HA answers,
   after checking states again. Until then den_lighting stays paused.
4. Restore each other bulb as in step 5 of the order, only if its state
   still shows the tool's last write. If a person is in the room, this sets
   untouched bulbs to den_lighting's normal scene, which is what den_lighting
   gives an arrival.
5. Turn automatic on, then call `resume_automatic` only for the restored
   bulbs.
6. Delete frames after the last clean step. Mark the session aborted. v1 does
   not fit or score aborted sessions.

If occupancy is unknown at resume, den_lighting sends nothing, so restored
bulbs stay at their normal level. den_lighting turns them off once occupancy
reads off again and 120 s pass. The tool says so.

If a game starts mid-run, `manual()` stops learning, so restore writes would
not reach `normal`, and the game's end writes back the normal scene saved at
its start. The tool then leaves den_lighting paused and asks the owner to run
`probe.py --recover` after the game ends.

One limit remains: a person can change a bulb in the fraction of a second
between the tool's re-read and its restore write.

A hard kill (window closed, PC crash) leaves den_lighting paused and the
bulbs in a probe state. The camera returns to auto by its own timer.
`probe.py --recover` finishes the job.

### Logging probes as non-human changes

`probes.jsonl` is the session's record. A new den-core adapter,
`sources.probe_log()`, turns it into den-event-1 lines:

- The tool's own writes, restores included, become `light_observed` with
  context `probe`.
- Foreign changes become `light_observed` with context `person`.
- Changes after resume become `light_observed` with context `unknown`.
- Occupancy readings become `occupancy` with `via: probe_gate`.
- The adapter never emits `correction`. Probes are kept out of corrections.

This follows den-core's README, "Adding a source adapter": one function, its
name added to `events.ADAPTER_SOURCES`, one `run.py` flag (`--probes`), and a
test. `events.validate()` does not check context values. The SHARED INTERFACE
text lists `'own'|'person'|'unknown'`, so it gains `'probe'`. Any later
correction learner must skip `probe`.

## Camera exposure

### What the installed camera can and cannot hold

From `tools/fixtures/installed-oak-capture-20261016.py`:

- **It holds nothing.** The RGB camera (CAM_A) runs the SDK's automatic
  exposure and white balance, and autofocus if the module has it. There is
  no `CameraControl`, no `setManualExposure` and no other control input in
  the file.
- **It reads one thing.** Each RGB frame's metadata has
  `exposure_duration_seconds`. It does not record gain (ISO), white balance or
  lens position.
- **It outputs ISP frames only.** The bundle's RGB plane is 640x360 NV12. The
  ISP has tone-mapped and gamma-encoded it. There is no RAW output. Both the
  640x360 output and the 1920x1080 RTSP stream use CROP from the 1920x1080
  sensor output. They share one sensor and one ISP, so they share exposure.
- **It already helps metering in one way.** It sets the IR dot projector and
  the IR flood light to 0.
- Bundles come at `--bundle-fps` (default 5). `/frame.npz` returns 503 when
  stale (default 15 s). Bundle metadata is schema 1.

Reading exposure from each frame is not enough. Auto-exposure moves gain and
white balance too, and neither is recorded. As A9 says, under auto-exposure
the pixel values barely move as light fades up.

### The least invasive way to held exposure

One small change to the camera server, `oak-capture-meter.py`:

- **A metering hold, off by default.** An authenticated `POST /meter` sets
  manual exposure and ISO, a fixed white balance, and the current lens
  position if the module focuses. The request carries a time to live of at
  most 120 s. The PC asks for 30 s and renews every 10 s.
- **It always returns to auto.** On `POST /meter/release`, on expiry, or on
  any server restart. A crashed PC tool can never leave the camera held.
- **POST always needs the token**, even on loopback.
- **Three more fields per RGB frame:** gain, white balance and lens position
  as the frame reports them, plus `meter: {held, generation}`. The schema
  stays 1, with keys added only. den-take's `parse_metadata()` ignores extra
  keys.
- **Nothing else changes.** RTSP, depth, the timing modes and `/health` stay
  as installed. A test checks that the file equals the installed fixture
  outside its marked meter blocks. perception-lab's bundle tests, which load
  the fixture, also run against it.
- The SDK's per-frame gain and white-balance getters and its manual
  white-balance and focus controls are checked against the Jetson's installed
  DepthAI version when the file is built. If any is missing, the install
  check shows it.

**Install, once, with approval.** Back up the script that
`systemctl cat oak-den.service` names, copy in the new file, and restart
`oak-den.service` while the den is empty. A camera restart drops Frigate's den
occupancy feed briefly, and the den lights follow that feed. In an empty den
with the lights already off, nothing should change. Then check `/health` and
that bundles show `held: false`. Rollback: copy the backup back and restart.

**During a session** Frigate's RTSP stream shares the held exposure. The room
must be empty anyway, and the camera guard backs Frigate up in dark phases.
The occupancy-gated activity loop has nothing to do in an empty room. Rung R
takes and rung 8 sessions never run together: one needs a person, the other an
empty room.

### Exposure tiers and the response curve

- **Bright tier.** Session 1 picks one exposure and gain so that, with all
  four bulbs at full and cool, the 98th percentile of Y stays below the
  METHOD limit. The bulbs themselves may clip. Their pixels are dropped.
- **Dim tier.** 8 times the bright tier's t·G. It prefers a longer time over
  more gain, within the 30 fps frame time. Each tier has its own all-off
  brackets.
- **Response sweep, session 1.** One bulb at full holds a still scene. The
  tool steps exposure from 1/16 to 2 times the bright tier in √2 steps. It
  also checks gain at (t, G), (t/2, 2G) and (t/4, 4G). `meter.py` fits a
  monotone inverse response per channel: alternating least squares on a
  piecewise-linear curve, made monotone with pool-adjacent-violators.
- **The global check.** One curve must fit the whole image within the METHOD
  residual, with no pattern across image regions. Gain steps must agree after
  dividing by t·G. Later sessions repeat three exposures to confirm the curve.

### Rejected options

- **Lock auto-exposure where it is.** The same server change, less control,
  and the values are whatever auto-exposure happened to pick.
- **RAW output.** A bigger change and more USB load. The server ends its
  session if its RTSP writer falls about 2 s behind. Kept as the fallback if
  the global check fails.
- **The mono cameras.** They would leave Frigate's RGB alone. But they have
  no colour, see near-infrared, and need the same kind of change.
- **A second process controlling the camera.** Impossible: the server is the
  one OAK owner.

## Tests (synthetic data)

All tests run offline on synthetic data, with fakes on 127.0.0.1 only:

```bash
python -B -m unittest discover -s tools/den-light -v
```

**`meter.py`**

- NV12 decode round-trips synthetic frames.
- The response fit recovers a known S-shaped curve within 1%.
- A synthetic local tone map fails the global check.
- Clipped pixels are dropped.
- Readings at the two tiers agree after dividing by t·G.

**`model.py`**

- A synthetic den with known B_c(p, k), two bulb types with different curves,
  linear-in-mired colour mixing, drifting daylight and noise. The fit recovers
  B and g within 1%, and the hold-outs pass.
- Negative cases fail: a saturating sum and a wrong response curve fail the
  gate, and a bulb whose curve depends on temperature fails the temperature
  check.
- The shared-curve check is true within a pair and false across pairs.
- The schedule is the same for the same seed, and no hold-out repeats a
  fitted step.

**Scoring**

- Percentiles match hand-worked values.
- The noise floor excludes and counts low pairs.
- A kind with fewer than 30 pairs fails.
- The absolute line always reads "not run".

**`guard.py`**

- Each preflight failure refuses: a hold, a game, a null normal field, a
  wrong sensor attribute, a bulb on, occupancy not off, the incumbent
  automation on, a moved revision.
- A clean run's call sequence is exactly: snapshot, pause, probes, restore,
  hold check, switch on, `resume_automatic`.
- A normal scene with `on` false restores as on, then off.
- Occupancy mid-run: no probe write after it, restore only untouched bulbs,
  resume only those.
- A foreign change on one bulb: that bulb is never written again.
- HA lost: no writes, `recover.json` written, and `--recover` finishes.
- A game starting mid-run: den_lighting stays paused and the tool asks for
  `--recover` after the game.

**den_lighting end to end.** The fake HA runs den_lighting's `policy.py`
unchanged, plus a copy of `service_called()`'s learning rule. After a full
synthetic session, `Controller.normal` equals the snapshot, `holds` is empty,
and the bulbs are off. The test pins `policy.py`'s sha256, as den-core does,
so a new den_lighting version fails loudly.

**`probe.py` end to end.** A fake camera renders frames from the synthetic den
under the held settings. A full session runs, then reduce, fit and score pass.
Each abort case leaves den_lighting resumed or recoverable, and no frames
after the abort.

**Camera server.** The meter hold's request checks, token rule, renewal,
expiry and release run against a fake control queue. The file matches the
installed fixture outside its meter blocks.

**den-core.** A synthetic probe log replays with no `correction` lines, and
`check.py` reproduces it byte for byte.

Synthetic runs prove plumbing. They are never evidence and never count toward
the gate.

## Deliberately left out

- Absolute lux and the k_L anchor: rung 7, waiting on photometry-commissioning.
- Occupied hold-outs and the visibility term
  V_total = (V_direct·E_dir + E_ind)/B. That is the next step after the empty
  room passes. It also needs the basis split into direct and indirect.
- Eye positions: computed, never captured, never scored.
- Glare.
- `xyz` and `normal` for sample points. v1 points are image rectangles. A
  later room registration fills them.
- A daylight model. D is measured per session. Prediction covers electric
  light.
- Live or continuous metering, and rung 9's coordinator.
- RAW capture and mono-camera metering.
- Any light other than the four Hue bulbs. Any Frigate configuration change.
- Automatic sample-point kinds.
- A den_lighting change that exposes `normal` in its status. It would remove
  the store-copy step, but it needs a deploy to the live integration.
- Fitting from aborted sessions.
- Probing while the older automation
  (`automation.den_lights_follow_person_occupancy`) drives the den instead of
  den_lighting. Preflight refuses then. A variant would pause that automation
  instead.

## Approvals needed

Each item needs the owner's yes before it happens. Nothing below has happened.

1. **Jetson change: install the metering camera server.** Copy
   `oak-capture-meter.py` in and restart `oak-den.service` once, while the den
   is empty. Frigate's den feed drops briefly, and the den lights follow that
   feed. Rollback is the backed-up script and one more restart.
2. **Camera change during each session: hold exposure, gain, white balance
   and focus** for about 12 minutes. Frigate, perception-lab and anything else
   reading the camera see held settings. It reverts by release, timer or
   restart.
3. **Light changes in each session.** About 150 captures on the four Hue
   bulbs over about 10 minutes: each bulb alone at full at three temperatures,
   11 dimming steps per bulb down to level 1, four warm steps on two bulbs, 16
   mixed scenes including all four at full, and a response sweep in session 1.
   The den must be empty. Planned: session 1 (about 12 minutes) and session 2
   (about 5 minutes), and a repeat only if one aborts or fails for a fixable
   reason. A yes can name a batch.
4. **Pausing den_lighting in each session.** The automatic switch goes off.
   At the end the tool writes the snapshot normal scene back as light
   commands, turns the switch on and calls `den_lighting.resume_automatic`.
   Each session also needs the store file copied, which reads HA but changes
   nothing.
5. **A Home Assistant long-lived access token on the PC** for `probe.py`. It
   is stored in `C:\den-light\private\` and never printed.
6. **Keeping the empty-room metering frames on the PC.** den-take's CHECK-V1
   records that the metering part of recording-retention is still open. The
   plan's default: keep frames on the PC until the owner deletes them, never
   copy them anywhere, and delete frames after an abort at once. If the owner
   prefers, `model.py` can delete frames after reduction. Changing sample
   points would then need a new session.

No decision blocks rung 8. Photometry-commissioning is not needed for it.

## Risks

- **The ISP response may not be one global curve,** for example local tone
  mapping. The global check in session 1 catches it within the first minute.
  Then the ISP route is closed, and RAW output would be a bigger camera change
  with its own approval.
- **The Jetson's DepthAI version may lack** a per-frame gain or white-balance
  report, or manual white balance in this pipeline. The install check and the
  frame-echo rule catch it.
- **Held exposure blinds Frigate somewhat in dark phases.** A person entering
  then might be missed by Frigate. The camera guard backs it up. Flashes may
  cause false person detections. Those abort the session, which is safe.
- **A stale or wrong store copy.** The revision check and the restored-hold
  check catch most cases. A wrong copy would set a wrong normal scene, which
  the owner can fix by setting the bulbs once by hand.
- **A restore can race a person's change** in a sub-second window.
- **den_lighting may change after 1.0.4.** The tests pin `policy.py`'s
  sha256. `probe.py` checks the store version and the sensor's attribute set,
  and refuses on a mismatch.
- **Short flashes run cooler than steady light.** LED output drops as it
  warms. The model describes short on-times. The start-and-end repeat
  measures drift. An optional ten-minute hold in session 2 would show the
  steady-state gap.
- **Daylight and stray light.** Sessions run after dark. All-off brackets and
  the drift check flag the rest. If the PC is in the den, its screen should
  stay off or on one still page.
- **8-bit frames at low levels.** The dim tier and averaging over rectangles
  and frames cover it.
- **REST polling** at about 1 s per entity can miss a sub-second flicker. The
  `last_updated` check and the camera guard cover it.
- **Port 8090 has never been reached from the PC.** den-take's ssh tunnel is
  the fallback, and POST works through it too.
- **Disk.** About 0.3 to 0.5 GB per session, on a PC drive den-take reports
  as 97% full.

## Owner time per probe session

| When | What | Time |
|---|---|---|
| Before | Copy den_lighting's store file and start `probe.py` | about 3 min |
| During | Keep the den empty and the household out | about 12 min (session 2 about 6 min) |
| After | Read the result line | about 1 min |

Once only:

- Create the HA token: about 3 min.
- Install the camera server by pasting commands: about 15 min.
- Draw sample points in `points.html` after session 1: about 10 min.
- Run `model.py` after each session: about 1 min.

## Effort

About 9 build days, all offline:

| Part | Days |
|---|---|
| `METHOD-V1.md` | 0.5 |
| `meter.py` and tests | 1.5 |
| `model.py` (reduce, schedule, fit, predict, score) and tests | 1.5 |
| `guard.py`, the den_lighting fake, and tests | 1.5 |
| `probe.py`, the loopback fakes, and end-to-end tests | 2 |
| `points.html` | 0.5 |
| `oak-capture-meter.py` and its tests | 1 |
| den-core `probe_log` adapter and test, README, change fragment | 0.5 |

Then about an hour of owner time across the install, two sessions and the
points page.

## What can be built and tested offline now, and what waits for approvals

**Now, with no approval:**

- `METHOD-V1.md`, `meter.py`, `model.py`, `guard.py`, `probe.py`,
  `points.html`, `synthetic.py` and all tests, against the synthetic den and
  the loopback fakes.
- `oak-capture-meter.py` and its offline tests. Writing it changes nothing on
  the Jetson.
- The den-core `probe_log` adapter.
- Whether the fit, the checks and the gate behave, including that they can
  fail.

**Waits for approvals:**

- Installing the camera server: approval 1.
- Any probe session: approvals 2 to 6.
- Whether the ISP output can be linearised: session 1's first minute.
- The gate verdict: after session 1, with session 2 as the day-to-day check.
- Occupied hold-outs and the visibility term: a later plan.
