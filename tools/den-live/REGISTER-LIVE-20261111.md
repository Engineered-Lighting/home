# REGISTER-LIVE-20261111: how the live den view registers the den camera

Written 2026-11-11, before any live registration ran. It's the method file for step 2 of the live den view. It closes
the slots den-register's REGISTER-V1 left open, and it sets the numbers `register_now.py` adds around den-register.
den-register itself runs unedited at the den simulation's pin (`tools/den-sim/refs.json`, `den-register`). It loads
this file in place of its own REGISTER-V1.md. A change to this file is a new method: date it as a new file and
register again.

## What changes from REGISTER-V1

- **The room positions come from the scan.** Each feature's room position is the point Home's own display-mesh picker
  (`createRegisteredDeskSurfacePicker`, the picker the sofa mark was made with) returns when you click the feature in
  your scan. It isn't taped. The markers draw in the scan's frame, so the registration has to land there.
  `register_now.py` records it as it is: each point says `measured_with: scan-pick`, the method says `scan-pick + fit`,
  and both are set in memory before den-register reads them, as the den simulation's `prepare.py` sets its own labels.
- **The gate is 0.10 m, not 0.05 m.** The markers need half a metre (the plan's pick 3). A clicked scan point costs a
  few centimetres, and the depth's own error below a few more.
- **The open slots close at their proposals.** Nobody has measured real click and depth noise yet, so the proposals
  are the only numbers there are.
- **The depth must be corrected at the camera.** See "The depth scale".

## The depth scale

On 2026-11-11 the den camera's raw depth read long against two tapes: the lens is 1.37 m above the floor, and the wall
it faces is 4.27 m away, square. The same day, the camera service took the correction on itself:
- Release `pilot-20261111-depth` (pull request 425) multiplies `depth_mm` by 0.889 before serving it. 0 stays 0.
- Every bundle's metadata, and `/calibration`, carry `depth_correction`:
  - `{factor, measured, calibration_sha256, applied}` when the correction is on;
  - `applied: false` with a reason when the camera's calibration isn't the one the factor was measured on, for
    example after a re-flash or with another camera. Then depth is raw again.
- Its check on served depth: the floor 1.395 m below the lens (+1.8%), the wall 4.171 m (−2.3%). The floor moves about
  ±2% with the light. The floor and the wall bend about 4.5° from square, which no single factor can fix.

So den-live applies no factor of its own: `depth_scale` is 1.0. A factor here as well would correct twice and read
11% short. Instead `require_source_correction` makes the paste check every recorded frame's `depth_correction`:
- If any frame says `applied: false`, or has no record at all, the paste stops before any clicking.
- The factor and the calibration hash it was measured on go into the paste's receipt.
- Stored takes from before the switch have no record, so `--again` on one of them stops too.

The `depth_scale` mechanism stays for one case: if the service's correction ever has to come off, a new method file
can set a factor here, and the floor guard says whether it was right. A scaled copy of the take is still written,
named `-x1`, so that path stays the one the tests run.

What's left after the correction:
- About 2% at the floor and 2% at the wall, in opposite directions.
- At the far wall that's about 10 cm along the ray, so a feature there can land near the 0.10 m gate on its own.
- If the fit names far features and every near one passes, that's this. The next step is the camera's proper
  (ChArUco) calibration, not a looser gate.

The fit stays rigid, Kabsch's least squares with no scale (REGISTER-V1, "The fit and the gate"). A scale in the fit
would hide a camera fault from every other reader of this depth.

## The two checks around the fit

- **The source check,** right after the take: every frame's `depth_correction` says `applied`, as above.
- **The floor guard,** before any clicking. It fits a plane to the empty-room depth by RANSAC, among points at
  least `floor_min_below_m` below the lens, with a normal within `floor_max_tilt_deg` of straight up in the camera's
  frame and an inlier band of `floor_band_m`. It compares the lens's height above that plane with
  `lens_height_tape_m`. If they're more than `floor_guard_fraction` apart, it stops, shows both numbers, and nothing
  is clicked. It also stops if under `floor_min_share` of those low points sit on the plane, because then it can't
  say where the floor is.
  - The band is 5%, about 7 cm, because served depth reads +1.8% at the floor and drifts about 2%.
  - A missing correction (+13%) or a doubled one (−11%) is still well outside it.
- **The features' own scale,** after the fit. A similarity fit (Umeyama) of the clicked features, camera to scan, gives
  the scale they'd pick. It never changes the fit. Outside 1 ± `own_scale_band`, it says the depth scale doesn't hold
  at those depths. With six features and a few centimetres of noise, it wanders by about 2–3% on its own. On a
  refusal it also tells the two causes apart:
  - With that scale every feature fits inside the gate: it's the depth scale, and re-clicking won't help.
  - One feature stays outside the gate: it's that feature's click or pick, and the summary names it.
  One wrong corner moves the scale too, so the scale alone can't say which.

## The numbers

den-register reads this block exactly as it reads REGISTER-V1's: one value per slot, none open.

```register-v1
min_fit_pairs = 3                 # REGISTER-V1, the ladder's rung 1
min_checks = 2                    # REGISTER-V1, the ladder's rung 1
gate_m = 0.10                     # this phase: the markers need 0.5 m (plan pick 3); REGISTER-V1 has 0.05
max_left_depth_skew_s = 0.067     # REGISTER-V1: one stereo frame
min_spread_m = 0.25               # closed at its proposal
depth_window_px = 3               # closed at its proposal: a 7 x 7 pixel square
min_depth_valid_fraction = 0.5    # closed at its proposal
mount_tolerance_mm = 50           # closed at its proposal
mount_tolerance_fraction = 0.03   # closed at its proposal
mount_changed_fraction = 0.5      # closed at its proposal
mount_min_cells = 16              # closed at its proposal
mount_view_reach_m = 6.0          # closed at its proposal
mount_min_spread_m = 0.4          # closed at its proposal
mount_min_reach_m = 3.0           # closed at its proposal
```

`register_now.py` reads this block. Every slot must be there.

```den-live-v1
depth_scale = 1.0                 # the camera service corrects depth itself since 2026-11-11 (x0.889); none here
require_source_correction = 1     # 1: every frame's depth_correction must say applied, or the paste stops
lens_height_tape_m = 1.37         # the lens above the floor, by tape, 2026-11-11
floor_guard_fraction = 0.05       # about 7 cm at 1.37 m: served depth reads +1.8% at the floor and drifts about 2%
floor_min_below_m = 0.3           # floor candidates: at least this far below the lens, in the camera's frame
floor_max_tilt_deg = 35           # the floor's normal, from straight up in the camera's frame
floor_band_m = 0.03               # RANSAC inlier band
floor_min_share = 0.10            # of the low points on the plane, or the guard can't say where the floor is
own_scale_band = 0.05             # the features' own scale, printed with a note outside 1 +/- this
```
