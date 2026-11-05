# OBSERVE-V1: how den-observe writes Observations

Rung 2 of `docs/architecture/live-v1/ladder.json`. Field names are the
`observation` record of `schemas.json`; this file says how each value is made.
Every number den-observe uses is in the block at the end. A slot marked `open`
is one the contracts leave open: an unscored run uses its proposed value and
lists it in the run file; `replay.py --scored` refuses while any slot is open.

## Inputs

- **Detector rows**, `den-detections-1` JSON Lines (see `detections.py`): per
  RGB frame, its capture time, boxes with class and score, and COCO-17
  keypoints with scores, plus `available_ts`, when the result was complete.
- **An R take** (`den-take-1`): per frame, the RGB and depth host-monotonic
  times and the rectified-left depth plane, from the same bundle.
- **A RoomModel camera entry**, which `transform_id` (`<camera_id>@<version>`)
  resolves to, from rung 1's `room-model.jsonl`: JSON Lines, one RoomModel
  revision per line, the last line the current room. A line that does not read
  refuses the run. An invalidation of the entry on any line holds, as rung 1
  resolves it. The contracts say `intrinsics` names its source, the EEPROM values
  today, and give no inner form; `den-observe-camera-1` (slot `camera_entry_form`)
  is the one den-register writes and this tool reads: `intrinsics` holds `source`, `rectified_left` and `rgb` ({fx, fy, cx, cy} or
  3x3), `rgb_distortion` (8 terms), `rgb_from_rectified_left` {rotation,
  translation_m}, `stereo_baseline_m`, `depth_size` and `rgb_size`; `pose` holds
  `camera_frame: "rectified_left"` and `room_from_camera`, a 4x4 metre matrix
  into the room frame (z up, floor at z = 0). Other keys in the entry are not read.

## Registration: refused, never guessed

Following `room_context.py`, the run is refused (nothing is written) when the
camera entry is missing, ambiguous (two entries for one `transform_id`), has no
rigid 4x4 pose or numeric intrinsics, has `error_bound_m` null (unvalidated) or
above `max_error_bound_m` (rung 1's gate), carries `invalidated` (rung 1's mount
check found the camera moved), or is **stale**: not the newest entry for its
camera in the current revision, or contradicted by a frame whose own intrinsics
differ from the entry's by more than 1 px (`depth_association.camera_model`'s
`frame_intrinsics_changed`).
Because a bad registration refuses the run, den-observe never writes the null
`room_position` and `joints_room` the schema reserves for an invalidated
transform.

A camera entry says where the camera was, so it can only lift a take recorded
while the camera was there. Rung 1's entry carries the two times that bound this:
`mount.take_ended_unix`, the registration's own take, and, once its mount check
has confirmed the registration on a later take, `confirmed.take_ended_unix`.
Against the take's own `ended_unix` in `take.json`:

- a take older than the registration's own is refused, scored or not: the camera
  may have been elsewhere, as when a knock and a new fit came between. No older
  entry is reached for: it would be stale, and it may be invalidated;
- a **scored** run also needs the take to be no later than the confirmation. A
  check confirms the camera had not moved by then, which is what vouches for the
  takes before it. So the owner runs the check after the takes to be scored;
- an unscored run of a take past the confirmation is allowed, and it says so:
  `run.json`'s `mount.status` is `not_confirmed` and the log has a `mount:` line.

A scored run refuses an entry with no mount record. An unscored one (the tests,
or a hand-made entry) lifts it and reports `no_mount_record`. Once a later check
invalidates an entry, it lifts nothing more, not even a take a confirmation had
vouched for: replay scored takes before the next check, or record new ones after
the next fit.

## Time

`capture_ts`, `depth_ts` and `available_ts` are Unix seconds:
`t_host + offset_s`, where `offset_s` comes from the take's PASS check (its
start-slate offset `c`, whole periods from the arrival clock; see `takes.py`).
Without a PASS check the arrival clock alone is used, which puts capture late
by the transport latency, and the run cannot be scored. Differences between
the three times are exact either way: one clock maps them all.

## Depth: used only when it belongs to the capture

The depth frame is the one in the same bundle as the RGB frame. It is used
only if `|capture_ts - depth_ts| <= max_depth_gap_s` (one stereo frame, from
`schemas.json`), as `registered_depth.py` measures depth age against the
source observation, never against processing time. `depth_ts` is written
either way, so the gap is visible. A frame whose depth is too old, or entirely
invalid, takes the plane fallback for every joint, and the gap is flagged in
`freshness.depth` (`too_old` or `absent`) with `freshness.depth_gap_s`.

## A joint: stereo first, then the declared plane

- **2D support**: a joint with a pixel inside the frame and score at least
  `keypoint_score_min`. Any other joint is `unsupported`, `lift: none`, with
  null `xyz` and `cov`: never zero-filled.
- **Stereo** (`lift: stereo`, `status: supported`): as `head3d.DepthLifter.lift`,
  every rectified-left depth pixel near the joint's RGB epipolar segment is
  deprojected at its own depth and projected into RGB through the EEPROM
  transform (`depth_association.py`); those landing within 4 px are the
  support, and the point is the per-axis median of their front surface, with
  at least 3 points.
- **Plane** (`lift: plane:<h>`, `status: uncertain`): when depth is unusable
  for the frame, or the joint has no stereo support, its RGB ray meets the
  horizontal plane z = h declared for its group: head (joints 0-4), arms
  (5-10), legs (11-14), feet (15-16). A ray that misses the plane in front of
  the camera, or whose along-ray deviation would exceed `plane_max_sigma_m`,
  gives `unsupported`, `lift: none`.

`room_position` of a keypoints Observation is the torso reference point: the
mean of the shoulders and hips (5, 6, 11, 12) lifted by stereo, or, when none
are, of those lifted by plane. A box Observation's is the robust median of the
depth landing in its inset box (`geometry.robust_support` rules); it has no
plane fallback. With no point, `room_position` is absent and the Observation
is rejected.

## Covariance

`cov` is A5's `Σ_room = R J Σ_meas Jᵀ Rᵀ + Σ_cal`, built per point from three
terms, then rotated into the room frame with the pose's R:

- **Depth noise**, along the viewing ray: `σ_along = z² σ_disp / (f_left B)`,
  stereo depth error for a disparity error `σ_disp = disparity_sigma_px`, left
  focal length `f_left` and baseline `B = stereo_baseline_m`.
- **Pixel noise**, across the ray: `σ_across = σ_px z / f_rgb`, with
  `σ_px = pixel_sigma_px` (depth_association's 4 px) at range z.
- **Registration**: `Σ_cal = error_bound_m² I`, the camera entry's bound read
  as one standard deviation per axis, added once.

A plane point replaces depth noise by the height doubt: `σ_along = σ_h / |d_z|`
for its ray's unit direction d and the group's `σ_h`, so a plane joint always
has a wider covariance than a stereo one. For the torso point, stereo noise is
independent per joint and divides by n; a plane's height error and `Σ_cal` are
common to every joint and do not.

## Status words

- `validity`: `valid`, or `rejected:<reason>` (slot `validity_form`; the
  schema says "enum + reason"). Reasons: `no_room_position:<why>`,
  `below_floor` (z more than 3 σ under the floor), `no_supported_joints`,
  `detector_result_expired`.
- `visibility`: `partial` if the box is within 4 px of the frame edge
  (`depth_association.floor_contact`'s margin) or a torso joint lacks 2D
  support; `occluded` if no torso joint has it; else `visible`.
- `score`: the box score. `source.model`: the keypoint model for a keypoints
  Observation, else the box model.

## Freshness

The gate asks every Observation to carry it; `schemas.json` has no field for
it, so it is written as `freshness` (slot `freshness_field`):

```text
"freshness": {"available_ts": 1793066610.71, "age_s": 0.29, "max_age_s": 1.0,
              "status": "fresh" | "expired" | "unknown", "valid_until": 1793066613.42,
              "depth": "used" | "too_old" | "absent", "depth_gap_s": 0.02}
```

`age_s = available_ts - capture_ts`. An expired result is rejected
(`detector_result_expired`). `valid_until = capture_ts + max_validity_s`
(`registered_depth.MAX_VALIDITY_S`, never extended). The contracts set no age
threshold: `detector_max_age_s` is open, with R5 as the baseline, where 115 of
228 results expired in the 2026-10-22 window (0.504) at about 1.46 s from
capture to completion. A scored run passes freshness only if its expired
fraction is at most `max_expired_fraction`.

## The block

```observe-v1
# From the contracts or reused from the repo
max_depth_gap_s = 0.067       # schemas.json Observation.depth_ts: one stereo frame (67 ms)
max_validity_s = 3.0          # registered_depth.MAX_VALIDITY_S
pixel_sigma_px = 4.0          # depth_association.PIXEL_UNCERTAINTY_PX
max_error_bound_m = 0.05      # ladder rung 1 gate: all within 5 cm

# Open: the contracts set none
detector_max_age_s = open     # open, proposed 1.0 (R5 baseline: 115 of 228 expired, about 1.46 s)
max_expired_fraction = open   # open, proposed 0.25 (R5 baseline 0.504)
keypoint_score_min = open     # open, proposed 0.4
disparity_sigma_px = open     # open, proposed 0.25 (about 2% depth error at 3 m)
plane_head_h_m = open         # open, proposed 1.55
plane_head_sigma_m = open     # open, proposed 0.30
plane_arms_h_m = open         # open, proposed 1.05
plane_arms_sigma_m = open     # open, proposed 0.35
plane_legs_h_m = open         # open, proposed 0.65
plane_legs_sigma_m = open     # open, proposed 0.25
plane_feet_h_m = open         # open, proposed 0.08
plane_feet_sigma_m = open     # open, proposed 0.05
plane_max_sigma_m = open      # open, proposed 2.0
freshness_field = open        # open, proposed "freshness"
validity_form = open          # open, proposed "rejected:<reason>"
camera_entry_form = open      # open, proposed "den-observe-camera-1"
```
