# Same-frame body observation diagnostics

`home-body-observations-1` is an optional diagnostic payload in
`home-fast-tracking-1.pose_observations`. It explains missing observations;
it does not supply an articulated body fit or a persistent person track.

The producer includes it only when explicitly provisioned with
`DEN_FAST_TRACKING_BODY_DIAGNOSTICS=1`. It remains behind the existing private
tracking read grant. Enabling it requires a reviewed deployment; it is off by
default. No image, video or depth array is included.

## Binding and lifetime

- `frame_key` equals the enclosing packet's exact frame key. Capture identity,
  scene binding, source clock interval and original expiry come from that same
  enclosing packet. The diagnostic payload never extends their lifetime.
- `association` is `frame_scoped_detection`. Each `people[].instance_id` matches
  an enclosing person from that exact inference. It is not a cross-frame track.
- `image_size` is `[640, 360]`; pixel coordinates refer to that source RGB frame.
- `camera_coordinate_space` is
  `rectified_left_x_right_y_down_z_forward_m`. Camera points are not room points.
- `point_semantics` is
  `visible_depth_surface_not_anatomical_joint_center`. Nominal stereo geometry
  and approximate room registration remain unqualified.

## Per-person joint records

Each person has `joints`, containing all 17 unique COCO joint IDs. Consumers use
IDs rather than array order. A joint has:

| Field | Meaning |
| --- | --- |
| `id` | COCO joint ID, 0 through 16 |
| `image_status` | `supported` or `uncertain`, from the exact model result |
| `image_reason` | Original omission reason, such as `low_response`, `outside_frame` or `nonfinite`; otherwise null |
| `pixel` | Original finite image coordinate, possibly outside the frame for an uncertain joint; otherwise null |
| `response` | Original finite RTMPose response; otherwise null |
| `response_semantics` | `SimCC_response_not_probability`; never calibrated probability |
| `depth_status` | `measured`, `unknown` or `not_attempted` |
| `depth_reason` | `no_depth_support`, `no_front_surface`, `image_joint_unsupported`, or null |
| `depth_support` | Number of retained support samples, or zero when not attempted |
| `depth_spread_m` | Existing depth-surface sample spread, or null |
| `camera_xyz_m` | Measured visible surface point in the stated camera frame, or null |

A supported image point with no stereo support stays explicitly `unknown` in
depth and has no 3D coordinate. An uncertain image joint never gets lifted.
Missing positions are null, never zero-filled, copied from another person or
promoted from earlier frames.

Existing `people[].joints[]` remains the set of depth-supported room observations.
No `body_pose`, `track_ref`, hidden limbs, floor contact, anatomical centers or
additional identity/lighting/physical authority is introduced by this payload.
Any future `home-body-pose-1` rig contract must separately distinguish observed,
estimated and missing rig joints and preserve the original source binding.

## Diagnostics and compatibility

Absent payload means these diagnostics were not supplied, not that all joints
were supported. Older projection sources without per-joint diagnostics may
continue producing the existing tracking packet without this optional field.
Camera/room coordinates never enter the structured journal trace; diagnostic
viewers must apply the existing private-evidence retention policy.
