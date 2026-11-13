# REGISTER-LIVE-20261113: registration v2 for the live den view

Written 2026-11-13 for step 02-1 of the perception plan, before any v2 session ran. It's the method file for step 2 of
the live den view from now on. `REGISTER-LIVE-20261111.md` stays as it was, for the receipts made with it. Like that
file, this one closes the slots den-register's REGISTER-V1 leaves open and sets the numbers `register_now.py` adds
around den-register. den-register itself runs unedited at the den simulation's pin (`tools/den-sim/refs.json`,
`den-register`), and loads this file in place of its own REGISTER-V1.md. A change to this file is a new method: date it
as a new file and register again.

## Why a v2

Two real-room fits were refused under v1. Most of the cause was the method, then the depth; the scan was ruled out.
- **The method.** Four fit points with leave-one-out is fragile; each click's depth came from one frame; and the advice
  asked for "table height" features, which are on furniture and often on an outer edge, where the depth window mixes
  the table with the floor behind it.
- **The depth.** Corrected depth isn't rigid. The floor and the facing wall want factors a few per cent apart, and its
  mean scale after the camera's move is known only to about 5%. No choice of clicks passes while the mean scale is off
  by a few per cent, so the session measures it.

## What changes from v1

- **Nine features, all on fixed structure.** Six fit (f1-f6: two at floor level, two at mid height, two high, left and
  right) and three checks (c1-c3) on fixed structure beside or behind the sofa, not the sofa itself. Inside corners
  or flat marks, never an edge with background behind it. No furniture, rugs, cushions, screens, glass or black fabric.
  Middle depths over the far wall's corners.
- **The take's median depth.** The copy den-register reads, `<take>-x<scale>-m`, holds in every bundle the per-pixel
  median of the take's valid depth (valid in at least `median_min_valid` of the frames, else 0), times den-live's depth
  scale. den-register still reads one frame; now that frame is the median.
- **The edge guard.** After the clicks, on the copy's depth: a click whose 7 x 7 window (den-register's
  `depth_window_px` 3) spreads, p90 - p10, by more than max(`edge_min_m`, `edge_fraction` x its depth) sits on a depth
  edge. The guard names it, and the click page opens again, up to `edge_rounds` times in one paste. The spread is taken
  about the window's own least-squares plane: the floor seen a few metres off changes depth by a centimetre or more a
  pixel, so a raw spread would name a good floor-level corner (on the den simulation's renders it named the set's own
  floor-level click), while a step between two surfaces is left whole.
- **The distance check,** after the scan picks and before the fit. A rigid fit keeps distances, so all 36 pair
  distances are compared, camera against scan. A feature whose median difference over its 8 pairs is over `gate_m`
  (0.15 m) is a wrong click or pick, which no fit could pass. It's named, worst first, in centimetres, and the paste
  stops. `--again` keeps everything and opens both pages again. Between `distance_max_m` (6 cm) and the gate, the
  feature is only noted in one line: a far feature's few centimetres of range error, or the depth's bend, are within
  what the owner allows, and on synthetic layouts a 6 cm stop caught them most of the time.
- **The depth check replaces the one-tape floor guard.** See "The depth scale".
- **The gate.** den-register gates every fit residual, leave-one-out error and check at `gate_m` 0.15 m. Then
  `register_now.py` requires each sofa check, c1-c3, within `sofa_check_max_m` 0.10 m. The owner's ruling of
  2026-11-13: "we don't need sub-centimetre accuracy; the beam is over a metre wide where it lands". A few centimetres at
  the sofa is plenty; the far features only need to catch gross errors.
- **Qualification on held-out depth.** See "The held-out take".
- **Room positions come from the scan,** as in v1: Home's display-mesh picker, recorded as `measured_with: scan-pick`
  and the method `scan-pick + fit`, both set in memory before den-register reads them.

## The depth scale

The camera service corrects depth itself (x0.889, release `pilot-20261111-depth`) and says so in every frame's
`depth_correction` record. With `require_source_correction` 1, a frame that doesn't say `applied` stops the paste.

On top of that, the session measures what's left, with two fresh tapes typed at the console: the lens's height above
the floor, and the lens to the facing wall, square to it. They're kept privately only, beside the take as soon as it's
recorded and in the receipt, and never printed.
On the take's median depth, a RANSAC floor plane (the lowest strongly supported near-level plane, as v1's guard found
it) gives the lens's height, and a RANSAC plane square to the floor and facing the camera, beyond
`wall_search_fraction` of the wall tape, gives the wall's distance. Only planes within `wall_tape_band` (10%) of the
wall tape count, and of those the farthest with at least half the best support wins, so a bookcase or wardrobe front in
front of the wall can't stand in for it; with no such plane the paste stops and says so. Both are perpendicular
distances, so the camera's pitch and yaw don't matter. Each is a signed percentage against its tape: floor % and wall %. Their difference is the
bend, in points; their mean is the scale error.

The rule:
- **Bend over `scale_bend_max_pts` (5 points): STOP.** The depth bends more than one scale can fix. The next step is the
  camera's own (ChArUco) calibration, the plan's question 3, never moving the camera.
- **|mean| at or under `scale_none_pct` (1.5%): no scale.** den-live's `depth_scale` is 1.0.
- **Otherwise `depth_scale` = 1 / (1 + mean),** rounded to 0.001. It must lie within 0.90-1.10; outside that the paste
  STOPs rather than clamp it.

The scale lives in den-live, not the camera (the owner's choice of 2026-11-13; there's no camera release). It's
applied to the median copy, so the clicks and the fit see scaled depth; it's recorded as the receipt's `depth_scale`;
and den-live multiplies every depth plane by it before any lift, the figure's depth included. den-live refuses to start
when the receipt's scale and the registration's own take disagree. At 1.0 it changes nothing.

The fit stays rigid, Kabsch's least squares with no scale (REGISTER-V1, "The fit and the gate"). The features' own
scale, a similarity fit (Umeyama), is printed as a diagnostic only, with a note outside 1 +/- `own_scale_band`.

## The held-out take

After a pass, a second 20 s take of the empty den, judged in the room.
- **Which pixels.** The scan's `points.ply`, from the active content pack (the scan revision the picks were made on,
  held to its manifest digest), is projected with the new registration into the left camera. Every point is
  z-buffered, covering its 3 x 3 pixels; a pixel well behind its 5 x 5 neighbourhood's nearest point is a gap in the
  points, not a surface. A pixel counts as floor when its nearest scan point is within `held_floor_band_m` of z = 0,
  and as upper wall when it's at or above `held_wall_min_z_m` (the ceiling included). Furniture hides what's behind it.
- **Their residuals, in the room.** The take's median depth times `depth_scale` is lifted through the new registration
  into the room. A floor pixel's residual is its height off z = 0. An upper-wall pixel's is its distance to the scan's
  surface there: the scan's points above `held_wall_min_z_m` less 25 cm, in 5 cm voxels, each a local plane; the
  distance to the nearest such plane within 15 cm, plus how far past its patch the point lies, and 20 cm where there's
  none. Depth along each pixel's ray, the first version's measure, was blind to a sideways shift or a turn (the floor
  and the facing wall slide along themselves) and oversensitive to a small tilt at the floor's grazing angle.
- **Within noise:** a residual at most max(`held_noise_min_m`, 2 sigma(z)), with z the pixel's range and sigma(z) =
  `noise_a_m` + `noise_b_per_m` z^2, the placement tool's stereo noise model.
- **Range bins** of `held_bin_m` by the pixel's range; a bin with under `held_bin_min_pixels` isn't judged.
- **A pass:** the floor and the upper walls are judged apart, so neither can outvote the other. Each needs at least
  `held_within_min` (75%) within noise and `held_min_pixels` pixels, and no range bin of either may have a median
  residual over `held_bin_max_m` (10 cm in the room, the owner's 2026-11-13 number; the plan had 5 cm).

The receipt says `qualified: true` only when den-register registered, each sofa check is within 10 cm and the held-out
take passes. den-live's installer, its service and its local run each require it.

## The numbers

den-register reads this block exactly as it reads REGISTER-V1's: one value per slot, none open.

```register-v1
min_fit_pairs = 6                 # f1-f6
min_checks = 3                    # c1-c3, beside the sofa
gate_m = 0.15                     # every residual, leave-one-out and check; the sofa checks then need 0.10 (below)
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

```den-live-v2
require_source_correction = 1     # 1: every frame's depth_correction must say applied, or the paste stops
median_min_valid = 0.5            # a pixel's take median needs depth in at least half the frames
scale_none_pct = 1.5              # |mean of floor % and wall %| at or under this: no den-live scale
scale_bend_max_pts = 5.0          # floor % and wall % further apart than this: STOP, the depth bends
floor_min_below_m = 0.3           # floor candidates: at least this far below the lens, in the camera's frame
floor_max_tilt_deg = 35           # the floor's normal, from straight up in the camera's frame
floor_band_m = 0.03               # the floor's RANSAC inlier band
floor_min_share = 0.10            # of the low points on the floor, or the check can't say where the floor is
wall_min_above_floor_m = 0.25     # facing-wall candidates: at least this far above the floor
wall_search_fraction = 0.75       # and at least this share of the wall tape ahead of the lens
wall_tape_band = 0.10             # a facing-wall plane only within this share of the wall tape's distance
wall_min_axis_cos = 0.7           # the wall's normal within about 45 degrees of the camera's axis
wall_max_floor_cos = 0.25         # and within about 15 degrees of square to the floor
wall_min_points = 300             # a facing wall needs this many inliers, or the check can't say where it is
edge_min_m = 0.04                 # edge guard: a click's window spread (p90 - p10) over max(this,
edge_fraction = 0.02              #   this x its depth) sits on a depth edge
edge_rounds = 3                   # the click page opens at most this often in one paste
distance_max_m = 0.06             # distance check: a feature's median pair difference over this is noted; over gate_m it stops
sofa_check_max_m = 0.10           # each sofa check, c1-c3, after den-register's gate passes
held_floor_band_m = 0.05          # held-out: scan points within this of z = 0 are floor
held_wall_min_z_m = 1.8           # and scan points above this are upper wall (or ceiling)
held_noise_min_m = 0.03           # within noise: a residual <= max(this, 2 sigma(z)), z the pixel's range
noise_a_m = 0.008                 # sigma(z) = a + b z^2, the placement tool's stereo noise model
noise_b_per_m = 0.0035
held_within_min = 0.75            # a pass: at least this share within noise, of the floor and of the upper walls each
held_bin_m = 1.0                  # range bins this wide, by the pixel's range
held_bin_max_m = 0.10             # and no bin's median residual, in the room, over this
held_bin_min_pixels = 200         # a bin with fewer pixels isn't judged
held_min_pixels = 2000            # fewer floor, or upper-wall, pixels than this: the take can't qualify
own_scale_band = 0.05             # the features' own scale, printed with a note outside 1 +/- this
```
