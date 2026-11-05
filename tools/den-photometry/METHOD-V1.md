# METHOD-V1: rung 7, measure light (experiment E8)

This is rung 7's method, version 1. It is committed alone, as the first commit
of `tools/den-photometry/` on `claude/rung-7-photometry`, before any code that
reads it and before any capture. `card.py` and `photometry.py` read every
number they use from the one fenced block below whose info string is
`method-v1 photometry`. Every `day.json`, `capture.json` and score `report.json`
records the sha256 of this file and of that block's text, both taken with
line endings read as LF, so a CRLF checkout hashes the same.

The contracts are `docs/architecture/live-v1/`: rung 7 in `ladder.json`, E8 in
`experiments.json`, and the `calibration_record`, `room_model` and
`light_state` records in `schemas.json`. Field names there are canon.

## Numbers

A line marked `open` holds a value the contracts leave to the owner, or one
this method proposes. Its value is a proposal only; an empty value has no
proposal the tool may use. While any line is marked `open`, `photometry.py`
refuses a new day, a capture and a scored run. The owner confirms or changes
each one, and the marks come off in one commit, before day 1. After day 1's
first capture the block never changes (see What may change): the meter
tolerance in lux is fixed before day 1, as the gate asks.

```method-v1 photometry
# The pass ({{dec:photometry-commissioning}}, not answered)
commissioning_option = card-and-meter   # open: the owner's option; this tool builds card-and-meter only
k_L_scope =                             # open: empty; single or per-illuminant-class
meter_tolerance_lx =                    # open: empty; held-out |E_hat - E_meter| allowed, in lux, fixed before day 1
meter_class =                           # open: empty; the meter's make, model and class, one word (for example class-B)
meter_sigma_percent = 5                 # open: one standard deviation of one reading, percent of reading, from the meter's class
meter_drift_max_percent = 5             # open: the before and after readings at a point may differ by at most this

# The card
rho_card = 0.18                         # open: the card's stated reflectance
rho_card_sigma = 0.01                   # open: one standard deviation of rho_card, from the card's stated tolerance

# Points, surfaces and days (ladder rung 7)
camera_id = den-oak                     # RoomModel.cameras[].camera_id of the den camera
fit_point = counter-1                   # open: the day-1 point whose card and reading fit k_L
required_points = desk sofa             # held out: at least these meter points
reflectance_surfaces = counter-1        # the reflectance pass: at least these surfaces (A12)
min_days = 2                            # at least two days
illuminant_classes = bulbs daylight mixed  # the light each capture is logged under
daylight_class = daylight               # at least one held-out day lit by window daylight

# One capture (rung 8's hold, two exposures and a repeat)
invariance_ratio = 0.5                  # open: hold 2's exposure time as a fraction of auto's pick
invariance_tolerance_percent = 3        # open: hold 1 and hold 2 may differ by at most this, per box
clipped_frac_max = 0                    # open: a box with a larger clipped fraction is not used
box_code_low = 16                       # open: every hold's mean code in a box, per channel, at least this
box_code_high = 235                     # open: and at most this
min_box_pixels = 100                    # open: a box needs at least this many pixels

# The known-card round trip (A12; fixed by the gate)
round_trip_C_card_nc = 100
round_trip_rho_card = 0.18
round_trip_E_meter_lx = 174.533
round_trip_k_L = 0.100                  # (cd/m2)/nc
round_trip_k_L_tolerance = 0.0005       # half a unit in the third decimal, as the gate states 0.100
round_trip_L_card_cd_m2 = 10.0
round_trip_L_tolerance = 0.05           # half a unit in the first decimal, as the gate states 10.0
round_trip_E_back_tolerance_lx = 0.0005 # half a unit in the third decimal, as the gate states 174.533
```

Each line is `name = value`, with an optional comment after `#`. A value is a
number, one or more words (letters, digits, `.`, `_` and `-`), or empty. Only
an `open` line may be empty. `card.py` refuses a missing, extra or repeated
name, and a word where a number belongs.

**Not in this block.** The hold's own numbers (the countdown, the still-scene
span, frames kept per exposure, the settle limit, the poll interval, the hold
time to live and renewal, the run limit, the exposure quantum and the camera's
exposure and ISO limits) are rung 8's, read from
`tools/den-light/METHOD-V1.md`'s `method-v1 stage0` block through `meter.py`.
meter-norm/v1's parameters (white balance, clipping limits, weights,
`normalise_code` and the three fitted curves) come from a passing Stage 0
sweep's `response.json`.

## What the tool reuses from rung 8

Nothing from `tools/den-light/` is copied. `photometry.py` imports `meter.py`
and `sweep.py` from there:

- **The hold**: `sweep.Camera` (the token, `/health`, `/frame.npz`,
  `POST /meter`, `POST /meter/release`), `sweep.camera_ready`, and a subclass
  of `sweep.Sweep` that keeps its countdown, still-scene wait, hold, renewal,
  settling, frame saving, release, Ctrl+C rule and void cleanup, and changes
  only which exposures it holds.
- **meter-norm/v1**: `meter.read_bundle`, `meter.decode`,
  `meter.meter_norm_value`, `meter.gain_agreement`, `meter.region_means` and
  `meter.agree`, with the parameters of a passing Stage 0 `response.json`
  whose `meter_norm.parameters_sha256` matches the canonical JSON of its
  parameters and whose version is `meter-norm/v1`.

The tool talks only to the camera. It never writes to any light, never pauses
or resumes den_lighting, and never talks to Home Assistant. A capture runs
under whatever light is on, and the owner logs that light with it.

## The session

**Folders.** Everything lives on the PC under one photometry folder, for
example `C:\den-photometry\`, outside any git checkout and outside OneDrive,
with at least 1 GB free. Each day is a folder `day-<yyyymmdd>\` made by
`--new-day`, holding `day.json`, `worksheet.csv` and one `capture-<n>\` per
capture. Days are ordered by their date; **day 1** is the earliest. A score
writes `score-<n>\` beside the days.

**A new day** (`--new-day`) pins the Stage 0 sweep: `day.json` records the
sha256 of its `response.json`, the normalisation version and parameters'
sha256, this file's hashes and rung 8's Stage 0 block hash. Every day of one
photometry folder must pin the same normalisation parameters and this file's
same block, or `--new-day` refuses.

**A capture** (`--capture`), at one point (`--point`, a surface or meter-point
name), with the light logged (`--illuminant`, one of `illuminant_classes`;
`--light`, the owner's words for what is on; `--blinds` open, closed or
unknown):

1. **Before anything.** It refuses (writing nothing) while any line here or in
   rung 8's Stage 0 block is `open`, when the day's pinned Stage 0 response,
   normalisation parameters or this block's hash differ from now, when rung
   8's white balance or clipping limits differ from the pinned parameters,
   and unless the camera answers healthy, unheld, with the meter keys
   (`sweep.camera_ready`). It also refuses a plan outside the camera's range
   (step 4), before any hold.
2. **Countdown** of rung 8's `countdown_s` while the owner steps out of view,
   then rung 8's **still scene** wait.
3. **Auto's pick.** t_pick and ISO_pick are the medians of the reported
   exposure (µs) and ISO over the still frames, as rung 8.
4. **Holds.** Hold 1 is t_pick at ISO_pick; hold 2 is
   round(t_pick · `invariance_ratio`) µs at ISO_pick; hold 3, the repeat, is
   hold 1 again. Every hold sets the pinned white balance. A hold below 1 µs,
   above rung 8's `max_exposure_us` or above its `iso_max` is refused before
   any hold.
5. Each hold keeps rung 8's `frames_per_step` frames after it settles (rung
   8's hold step). Then the release, on every path.
6. **Repeat check.** The first kept frame of hold 1 and of hold 3 must agree
   (rung 8's region means within `noise_tolerance_codes`). If not, the light
   or the scene changed: void.

A void records its reason (rung 8's reasons) and deletes the frames. A kept
capture writes `capture.json`, its frames (`frames\`, `frames.jsonl`, the RGB
plane and metadata only) and `preview.bmp` (hold 1's first frame, 640 × 360),
and appends one row to the day's `worksheet.csv`.

**The worksheet** (`worksheet.csv`, comma separated, UTF-8). The tool fills
`capture`, `point`, `illuminant`, `blinds`, `light` and `captured_local`;
the owner must not change them. The owner types:

- `E_before_lx` and `E_after_lx`: the meter in the card's plane, beside the
  card, read just before the countdown and just after the release, in lux.
  Both or neither.
- `card_box`: the card's pixel box in `preview.bmp`, `x0 y0 x1 y1` (left, top,
  right, bottom, inclusive, 0 ≤ x ≤ 639, 0 ≤ y ≤ 359), drawn inside the
  card's edges. Needed by any row with a reading or a `surface_box`.
- `surface_box`: for the reflectance pass, a box on the surface named by
  `point`, beside the card and in the same plane, on a matte part of it.
- `note`: anything.

A row with nothing typed is not used, and the score lists it.

## The math

All values are meter-norm/v1 normalised counts (nc), the frame's own reported
exposure t (ms) and G = ISO / 100.

**A box in a hold.** For each kept frame j of hold h, c_j is the mean of
`meter.meter_norm_value` over the box's pixels that meter-norm/v1 does not
flag (the contracts' C(s): the mean over unclipped, unmasked pixels). C_h is
the mean of c_j over the hold's frames and sem_h their standard deviation over
√n (0 for one frame). The box's clipped fraction `clipped_frac` is its
flagged pixels over all its pixels, in holds 1 and 2.

**A box is usable** when it has at least `min_box_pixels` pixels, its
`clipped_frac` is at most `clipped_frac_max`, every hold's mean code in it is
within `box_code_low`..`box_code_high` in every channel, and every kept frame
of holds 1 and 2 passes rung 8's gain agreement against its hold.

**The box's reading.** C = (C_1 + C_2) / 2, with standard uncertainty
u_C = √((sem_1² + sem_2²) / 4 + (C_1 − C_2)² / 4): the frames' scatter and
half the exposure disagreement.

**Exposure invariance.** Per usable box, d = 100 · |C_1 − C_2| / C percent,
with u_d = 100 · √(sem_1² + sem_2²) / C. It passes when d ≤
`invariance_tolerance_percent` for every usable box of every capture the score
uses. A normalisation that leaves an offset (a black level or flare that
meter-norm/v1's curve does not remove) shows here, because only a
proportional reading survives division by t·G at two exposures. Hold 2 changes
time only; gain is not stepped, because auto's ISO may already be the
camera's lowest.

**The meter reading.** E = (E_before + E_after) / 2, with relative
uncertainty u_E/E = √((`meter_sigma_percent`/100)² + ((E_before − E_after) /
(2E))²). A reading whose before and after differ by more than
`meter_drift_max_percent` of E is not used (the light changed).

**The camera scale (K21).** From the fit capture's card:

  k_L = ρ_card · E / (π · C_card), in (cd/m²)/nc,

with u_k/k = √((`rho_card_sigma`/ρ_card)² + (u_E/E)² + (u_C/C)²).

**The production path (K11).** L = k_L · C in cd/m², and for a matte surface
of reflectance ρ, Ê = π · k_L · C / ρ = π · L / ρ in lux.

**The round trip** runs the two functions the score uses, K21's k_L and
K11's L and Ê, on the gate's numbers: C_card 100 nc, ρ_card 0.18 and E_meter
174.533 lx must give k_L 0.100 (cd/m²)/nc within
`round_trip_k_L_tolerance`, L 10.0 cd/m² within `round_trip_L_tolerance` and
Ê back 174.533 lx within `round_trip_E_back_tolerance_lx`. It also needs the
normalisation version of every frame the score uses to equal the version of
k_L's inputs, `meter-norm/v1`. It is written to
`CalibrationRecord.checks.round_trip`. `--self-check` runs it with no camera
and no data.

**Held-out checks.** Each held-out capture's card gives
Ê_j = π · k_L · C_j / ρ_card, and the error e_j = Ê_j − E_j in lux, with
u(e_j) = √((Ê_j · r)² + (u_E_j)²), r = √((u_E/E)²_fit + (u_C/C)²_fit +
(u_C/C)²_j). ρ_card cancels (Ê_j = E_fit · C_j / C_fit), so the checks test the
camera's scale against the meter, not the card's stated reflectance. Each
passes when |e_j| ≤ `meter_tolerance_lx`. The record also gives
`residual_lx_rms` over `n_points`.

**Reflectance.** For a capture with a `surface_box` at surface s, q = C_s /
C_card from the same frames, u_q/q = √((u_C/C)²_s + (u_C/C)²_card). Several
captures of s are combined by inverse-variance weights on q (a plain mean when
any u_q is 0). Then ρ_s = ρ_card · q̄, with u_ρ/ρ = √((`rho_card_sigma`/ρ_card)²
+ (u_q̄/q̄)²). No camera scale is needed, and a scale error cannot change it.
The card must lie flat on s (in its plane, beside the box), and s must be
matte where the box is.

## Which captures count

A capture counts only when it is kept (not void) and its frames hash what
`frames.jsonl` recorded. A row with a reading or a `surface_box` but no
`card_box` is refused: the score stops and names it. The score lists every
capture it does not use, with the reason: nothing typed, a box not usable, a
reading not used, or a repeat of the fit.

**Groups.** With `k_L_scope` single, all captures form one group. With
per-illuminant-class, each `illuminant` class is a group with its own k_L.

**The fit.** A group's day 1 is the earliest day holding any of its captures.
Its fit capture is the earliest usable capture with a used reading at
`fit_point` on that day. A group with no fit capture has no k_L.

**Held out.** Every other usable capture of the group with a used reading,
except other captures at `fit_point` on the fit's day (they repeat the fit and
are not independent).

**Coverage.** The held-out checks are complete when a group's fit and held-out
captures span at least `min_days` days, the held-out captures include every
point in `required_points`, at least one held-out capture is logged under
`daylight_class`, and every held-out capture's group has a k_L. The
reflectance pass is complete when every surface in `reflectance_surfaces` has
at least one usable reflectance capture.

## Verdicts

- `checks.round_trip.result`: pass or fail.
- `checks.exposure_invariance.result`: pass or fail, before scoring.
- `checks.held_out.result`: pass when complete and every |e_j| is within
  tolerance; fail when complete and any is not; `incomplete` when coverage is
  missing; `not scored` when the round trip or exposure invariance failed.
- **Rung 7's gate** passes when all three pass and the reflectance pass is
  complete. The score prints PASS, FAIL or INCOMPLETE.

## The records

Each score writes JSON Lines (one record per line, UTF-8, LF) in
`score-<n>\`, with the contracts' field names. Each line also carries
`record` (the record's id in `schemas.json`) and `contract`
(`live-v1/B`). Nothing here is deployed or sent anywhere.

- `calibration_record.jsonl`: per group, `parameter`
  `RoomModel.cameras[<camera_id>].photometric.k_L`, `value`, `unit`
  `(cd/m²)/nc`, `method` `meter`, `confidence_pct_1sigma` (u_k/k in percent),
  `inputs` {`normalisation_version`, `rho_card`, `E_meter_lx`, `C_card_nc`,
  `exposure` {`time_ms`, `gain`, `black_level`}, `clipped_frac`,
  `illuminant`}, `checks` {`round_trip`, `held_out`, `exposure_invariance`},
  `ts`, `version` (`cal-<camera_id>-kL-<score n>`, with the class appended
  per class), `supersedes` and `supersedes_method` / `supersedes_value` when
  an earlier score exists. `inputs` also carries
  `normalisation_parameters_sha256`, additive: meter-norm/v1 is a rule plus
  fitted curves, and a k_L belongs to one fit of them. `black_level` is null:
  meter-norm/v1 has no separate black level (its fitted curve maps codes to
  light), and exposure invariance tests what is left. Then per reflectance
  surface, `parameter` `RoomModel.surfaces[<id>].reflectance.value`, `unit`
  `fraction`, `method` `meter` (the enum has no card-only method),
  `confidence_pct_1sigma`, `inputs` {`normalisation_version`, `rho_card`,
  `C_card_nc`, `C_surface_nc`, `clipped_frac`, `illuminant`} and `version`
  `cal-<camera_id>-rho-<surface>-<score n>`.
- `room_model.jsonl`: the input RoomModel with a new `version`
  (`<old>-p<score n>`), `cameras[<camera_id>].photometric` {`k_L`, `unit`,
  `normalisation_version`, `calibration_id`, `status`} and the measured
  surfaces' `reflectance` {`value`, `status` `measured`, `calibration_id`}.
  `status` is `measured`, with k_L, only when rung 7's checks pass;
  otherwise it is `uncalibrated` with `k_L` null and `calibration_id` naming
  the record that did not pass. Per class, `k_L` is null and an additive
  `k_L_by_illuminant` holds one k_L per class (an owner question: the contract
  has room for one). Reflectance is written as measured only when exposure
  invariance passes.
- `light_state.jsonl`: one per used capture: `ts` (the PC's clock when hold
  1's first frame arrived), `camera_id`, `room_version` (the new version),
  `exposure` {`time_ms`, `gain`, `black_level` null, `wb_locked` true,
  `linear` true, `normalisation_version`} of hold 1, `anchored`, and
  `surface_estimates` [{`surface_id` (the point), `value`, `unit`,
  `clipped_frac`, `rho_status` `measured` (the card's), `basis`
  `measured`}] from the card. `anchored` is true only when the frame's
  normalisation version equals k_L's and k_L's record passed its round trip,
  exposure invariance and held-out checks; then `value` is Ê in `lx`.
  Otherwise no lux is claimed: `value` is π · C / ρ_card with `unit`
  `relative` (lux divided by the camera scale). `window_daylight` is empty
  (v1 measures no window region), `sun` is computed from the time and the
  location given to `--score` (azimuth clockwise from true north; null
  without a location, which is never stored), `blinds` is as logged and
  `residual` is empty (rung 10 fills it).
- `report.json`: every capture's statistics, the exclusions with reasons,
  the groups, the coverage, the verdicts, and the hashes of this file, rung
  8's Stage 0 block, the pinned response and the code.

## E8's daylight day

E8 also asks to track window daylight for a full day against sun position and
blind state. In v1 that is a day of captures through the day, logged under
`daylight_class` with the blinds as they are: each gives lux at its point (a
held-out check), the sun's position and the blinds in its LightState. No
window region is measured.

## What may change

Before day 1's first capture, this file may be revised in place, in its own
commit, and the code follows. After it, the block never changes: a new rule
is `METHOD-V2.md` with a new photometry folder, and day 1 starts again. Every
day and capture records the block's hash, and a score refuses captures taken
under another block.
