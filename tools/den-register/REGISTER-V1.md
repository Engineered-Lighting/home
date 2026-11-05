# REGISTER-V1: how rung 1 registers the den camera

Written before any den run. It binds rung 1 of
`docs/architecture/live-v1/ladder.json` ("Camera registration") to the records
in `docs/architecture/live-v1/schemas.json` (`room_model`,
`calibration_record`). A change to this file is a new method: commit it as
REGISTER-V2 and register again.

## What is measured

- **Control points.** Small marks (a tape cross is enough) on flat surfaces the
  camera sees: floor, table top, counter, wall. The owner measures each mark's
  room-frame position with a tape or a laser from named references and writes
  it in the worksheet, with its role: `fit` or `check`. A check point is never
  used by the fit.
- **Camera side.** The owner clicks each mark on the rectified left image of
  one frame from an R take. The depth there is the median of the valid depth
  pixels in a `(2 * depth_window_px + 1)` square around the click, from the
  same bundle's depth plane, which is aligned to the rectified left camera. The
  click and the depth give a camera-frame point through the depth frame's own
  intrinsics (the camera's EEPROM values).
- **Room frame.** The contracts' frame, `z_up_metric_floor0`: metres, z up from
  the floor, x along the room's long axis, origin at the minimum corner of the
  scan. A worksheet in any other frame is refused.

## The fit and the gate

The fit is a rigid transform, room = R · camera + t, with no scale and a proper
rotation (Kabsch's least squares, solved as Horn's quaternion eigenproblem), as
`stack/services/perception-lab/spatial_validation.py` fits it. The ladder's
gate is three non-collinear control pairs plus two independent checks, **all
within 5 cm**. A registration is accepted only if all of these hold:

1. At least `min_fit_pairs` clicked fit points and `min_checks` clicked check
   points (`too_few_pairs`, `too_few_checks`).
2. The fit points' second principal RMS spread in the room frame is at least
   `min_spread_m`: they are not on one line (`collinear`).
3. The camera sits above the floor: a mirrored worksheet can fit coplanar floor
   points by flipping the floor (`camera_below_floor`).
4. Every fit point's residual is at most `gate_m` (`fit_residual_over_gate`).
5. Every check point, carried through the fitted transform, lands within
   `gate_m` of its measured position (`check_over_gate`).

Reported, not gated: the RMS residual, the leave-one-out error of each fit
point when there are four or more (with three, two remain and cannot fix a
rotation, so it is not computed rather than guessed), and `error_bound_m`, the
largest of the fit residuals, the check errors and the leave-one-out errors.

Refused before any fit, each with its name: open slots in the block below
(`slots_open`), a worksheet that does not read (`worksheet_invalid`), a take
or bundle that does not read (`take_unreadable`, `bundle_unreadable`,
`frame_missing`), no intrinsics in the depth frame (`intrinsics_missing`), a
left image and depth plane from different stereo frames or more than
`max_left_depth_skew_s` apart (`frame_depth_different_times`), a click with too
little depth around it (`depth_missing`), clicks saved for another frame or for
ids the worksheet does not hold (`clicks_stale`), a point whose optional scan
position differs from its tape position by more than `gate_m`
(`scan_disagrees`), a take recorded before a mount check that found the camera
moved (`mount_changed`), and the same inputs registered already
(`already_registered`).

## What is written

On acceptance, one RoomModel revision is appended to `room-model.jsonl` and one
CalibrationRecord to `calibration-records.jsonl`, both JSON Lines, UTF-8, LF.

- The RoomModel's camera entry has the contract's fields `camera_id`,
  `intrinsics` (with its `source`), `pose`, `method` (`tape + fit`), `version`,
  `error_bound_m` and `photometric` (carried over from the previous entry, or
  `uncalibrated`), plus `calibration_id` and `mount`. `pose.room_from_camera`
  is a 4x4 rigid matrix from the rectified left camera frame (+x right, +y
  down, +z forward, metres) to the room frame.
- `version` is the take's local date, `YYYY-MM-DD.N`, where N counts this
  camera's registrations in the file. It is never reused, so an Observation's
  `transform_id`, `<camera_id>@<version>`, resolves to exactly one entry.
- The CalibrationRecord's `parameter` is `RoomModel.cameras[<camera_id>].pose`,
  its `value` the same matrix, with `residual_m_rms`, `residual_m_max`,
  `n_pairs`, `error_bound_m`, `inputs` (worksheet, take, frame, clicks and every
  pair), `checks` (`held_out`: the two or more check points; `round_trip`: one
  fit point resolved back through the written `transform_id`) and `ts`,
  `version`, `supersedes`.

## A moved camera

A registration holds only while the camera stays where it was. The camera
entry keeps a mount signature: the 8x8 grid of per-cell median depths of the
registration frame. `check-mount` compares a later frame with it, as
`spatial_validation.check_mount` does. `changed` appends a RoomModel revision
whose entry carries `invalidated`, with what the owner must re-check; from then
on that `transform_id` resolves as invalidated, and a fit from a take recorded
before that check is refused. `unknown` (too few comparable cells, for example
a person in front of the camera) invalidates nothing and confirms nothing.

## The numbers

A value is a number, or `open` with a proposed value. The click page uses the
proposed values so the owner sees depth while clicking; `fit` and `check-mount`
refuse while any slot is open. Closing a slot is a commit to this file before
the scored run.

```register-v1
min_fit_pairs = 3                 # ladder rung 1: three non-collinear control pairs
min_checks = 2                    # ladder rung 1: plus two independent checks
gate_m = 0.05                     # ladder rung 1: all within 5 cm
max_left_depth_skew_s = 0.067     # Observation.depth_ts: one stereo frame (67 ms)
min_spread_m = open               # open, proposed 0.25 (spatial_validation.fit_rigid's default)
depth_window_px = open            # open, proposed 3 (a 7 x 7 pixel square)
min_depth_valid_fraction = open   # open, proposed 0.5 of that square
mount_tolerance_mm = open         # open, proposed 50 (spatial_validation.check_mount)
mount_tolerance_fraction = open   # open, proposed 0.03 of the cell's depth
mount_changed_fraction = open     # open, proposed 0.5 of compared cells
mount_min_cells = open            # open, proposed 16 comparable cells
```
