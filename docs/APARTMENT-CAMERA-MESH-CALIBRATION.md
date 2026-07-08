# Apartment Camera/Mesh Calibration Plan

The `/apartment` camera view is intended to be a calibrated mixed view:
the live camera image stays uninterrupted, while the black letterbox space above
and below the video is filled by the same 3D apartment geometry from the exact
camera pose. The mesh may also be drawn as a subtle overlay when useful, but the
video feed itself must remain readable.

## Root Problem

This is not primarily a CSS layout problem. A correct result requires the
renderer to know each camera's real intrinsics, distortion/crop behavior, native
stream size, and extrinsics in the apartment model coordinate frame. If any one
of those is missing or stale, the video can load quickly but still fail to align
1:1 with the mesh.

## Data Contract

Each camera used by Apartment mode should have a calibration record with:

- `camera_id` matching the Home app camera id.
- `video_width` and `video_height` for the stream actually rendered.
- Intrinsics: `fx`, `fy`, `cx`, `cy`, plus distortion coefficients if needed.
- Extrinsics: `camera_from_world` or `world_from_camera`, with units and handedness documented.
- Stream crop/fit mode: whether the source is letterboxed, cropped, or scaled.
- `rms_px` or another reprojection-error score from the calibration pass.
- `updated_at` and asset/model version.

## Implementation Phases

1. Inventory the current assets and camera metadata: `manifest.json`,
   `frame.json`, mesh/splat files, camera device ids, and any saved pose files.
2. Add an asset validator that reports missing calibration fields per camera.
3. Add a debug render mode that draws projected mesh edges and calibration
   control points over a frozen camera frame.
4. Calibrate one camera end to end, then repeat for all interior cameras.
5. Update `/apartment` camera mode to render the mesh outside the video
   letterbox area using the same calibrated camera projection.
6. Add Playwright screenshot assertions for mobile and desktop camera views.

## Acceptance Criteria

- The camera video loads quickly and does not flicker gray.
- The video remains uninterrupted and readable.
- Mesh geometry above and below the video is visible where the calibrated view
  projects geometry into the letterbox space.
- Mesh/video edges align at the video boundary within the chosen RMS threshold.
- Switching cloud/photo/mesh modes before or after camera snap does not break
  the calibrated camera view.
- Mobile screenshots cover portrait and landscape, including Safari-sized safe
  viewport constraints.

## Non-Goals For The Next Pass

- Do not hand-tune CSS offsets as the primary alignment mechanism.
- Do not assume all cameras share the same intrinsics.
- Do not treat a loaded video feed as proof of calibration.
- Do not bundle Apartment assets into desktop installers.
