# REGISTER-V1: how rung 1 registers the den camera

Written before any den run. It binds rung 1 of
`docs/architecture/live-v1/ladder.json` ("Camera registration") to the records
in `docs/architecture/live-v1/schemas.json` (`room_model`,
`calibration_record`). A change to this file is a new method: commit it as
REGISTER-V2 and register again. Before any registration had run, the 2026-11-05
review's fixes changed REGISTER-V1 in place: "A moved camera" below no longer
lets a depth comparison confirm a registration, needs every mark and the whole
view to agree before it confirms, lets only the marks moving together invalidate,
and writes a confirmation where rung 2 reads it; the camera entry now holds the
geometry rung 2 needs; and a leave-one-out error over the gate is refused, because
rung 2 refuses the bound it would write.

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
6. With four or more fit points, each one's leave-one-out error is at most
   `gate_m` (`leave_one_out_over_gate`). With three, two remain and cannot fix a
   rotation, so it is not computed rather than guessed.

`error_bound_m` is the largest of the fit residuals, the check errors and the
leave-one-out errors, so a registration's bound is never over the gate. Rung 2
refuses one that is, and this refuses it here, where a point can be re-measured.
Reported, not gated: the RMS residual.

Refused before any fit, each with its name: open slots in the block below
(`slots_open`), a worksheet that does not read (`worksheet_invalid`), a take
or bundle that does not read (`take_unreadable`, `bundle_unreadable`,
`frame_missing`), no intrinsics in the depth frame or no intrinsics or distortion
in the RGB frame (`intrinsics_missing`), no saved or usable /calibration reply
(`camera_calibration_missing`, `camera_calibration_invalid`), a left image and
depth plane from different stereo frames or more than
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
  `uncalibrated`), plus `calibration_id` and `mount` (the registration take's
  end time, its depth signature and each mark's camera-frame position, for "A
  moved camera" below). `pose.room_from_camera`
  is a 4x4 rigid matrix from the rectified left camera frame (`camera_frame`
  `rectified_left`; `camera_axes` +x right, +y down, +z forward, metres) to the
  room frame.
- The contract says `intrinsics` names its source, the EEPROM values today, and
  every later Observation's `transform_id` resolves to this one entry. So it
  holds the form rung 2 reads (den-observe's `den-observe-camera-1`): `source`;
  from the registration frame's own metadata, `rectified_left` and `rgb`
  ({fx, fy, cx, cy}), `rgb_distortion` (8 terms), `depth_size` and `rgb_size`;
  and from the camera's /calibration reply, which no take saves and the owner
  saves once as `camera-calibration.json`, `stereo_baseline_m` and
  `rgb_from_rectified_left` {rotation, translation_m}, composed as
  `depth_association.eeprom_rgb_from_rectified_left` does.
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

A registration holds only while the camera stays where it was. `check-mount`
looks at a frame of a later take in three steps.

1. **A coarse depth alarm.** The camera entry keeps a mount signature: the 8x8
   grid of per-cell median depths of the registration frame. A later frame is
   compared with it, as `spatial_validation.check_mount` does. `changed` (more
   than `mount_changed_fraction` of the compared cells past their tolerance)
   invalidates at once. Nothing else this step says counts as evidence: a 5°
   turn or an 8 cm slide moves room points by 8 to 40 cm and changes under half
   the cells, because a floor's depth does not change when a level camera turns
   about the vertical. Depth noise limits how far the slots could tighten.
2. **The marks, against the same gate.** Depth alone never confirms. The owner
   clicks every mark on that later frame with the `click` step. A turn shows most
   at the far marks, so any mark the registration used and this frame does not
   have clicked gives `not_confirmed`, naming it. With every one clicked, their
   camera-frame positions on the two frames (the entry keeps the first as
   `mount.marks`) give the rigid motion they show together: `moved_m`, the
   farthest it carries a mark, and `off_m`, the most a mark is off it.
   - `moved_m - off_m` over `gate_m`: `changed`. A moved camera carries every mark
     with it, so this is what says the camera moved, past how much the marks
     disagree among themselves;
   - a mark `off_m` over `gate_m` from that motion: `not_confirmed`, naming it.
     Its click is off, or something moved it on its own;
   - a mark more than `gate_m` from its tape position, carried through the
     registered transform: `not_confirmed`, naming it. A mark the fit accepted near
     the gate has no margin, so half a pixel can put it past;
   - otherwise the third step decides.

   Neither of the last two invalidates. `changed` cannot be undone and it loses
   the takes a confirmation vouched for, so one mark's click or depth never makes
   it. A real knock that this leaves short of `changed` is still never confirmed.
3. **The whole view, against the same gate.** A mark within the gate says little
   about a point elsewhere: marks near the optical axis hardly show a roll, and
   a swing about the marks' centre hardly moves them. So `view_movement_m` is the
   farthest the marks' rigid motion carries anything in view: the marks, the
   camera, and the corners of each signature cell at its depth, capped at
   `mount_view_reach_m`. A cell
   with no depth counts at `mount_view_reach_m`: a dark far floor or a bare wall
   has no depth and is still room. How far a rigid motion moves a point is convex
   in the point, so nothing between the camera and those surfaces moves further.
   At most `gate_m`: `confirmed`. Over it: `not_confirmed`. So is a registration
   frame with under `mount_min_cells` cells of depth, which cannot say what the
   view holds.

`changed` appends a RoomModel revision whose entry carries `invalidated`, with
what the owner must re-check; from then on that `transform_id` resolves as
invalidated, and a fit from a take recorded before that check is refused.
`confirmed` appends a revision whose entry carries `confirmed`: the take, its
`take_ended_unix`, the frame, `worst_mark_m` and `view_movement_m`. Only a later
take than the one already there moves it on. `not_confirmed` writes nothing and
confirms nothing. A take not recorded after the registration's own take is
refused (`mount_take_not_later`), so a registration never confirms itself.

A confirmation says the camera had not moved when that take was recorded, so it
vouches for what came before it, back to the registration's own take. Rung 2's
scored replay holds a take to that span (its METHOD.md, "Registration"). So the
owner checks after the takes to be scored, not only before them.

A person in view can go either way. Cells that lose their depth give `unknown`,
which confirms nothing. But a person has valid depth: one filling over half of
the frame trips the alarm, and the unmoved camera's entry is invalidated for
good, at the price of a new take and fit. So the check take is recorded with the
room clear.

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
mount_view_reach_m = open         # open, proposed 6.0 (past the room's far corner: a window or a doorway)
```
