# Coordinate frames — the one contract everything obeys

## Apartment frame (storage + backend + tracker)
- **Right-handed, Z-up, meters. Floor plane z = 0.**
- Origin: min-corner of the floor's oriented bounding box (apartment lives in +X/+Y).
- +X along the apartment's long wall axis.
- Floor polygons / zone polygons: `[x, y]` pairs, CCW winding.
- Positions: `[x, y, z]` (z = height above floor).
- Produced by `tools/spatial-pipeline/20_register_frame.py` (`registration.json` holds
  `T_mesh` and `T_splat`, scan frame → apartment frame).
- The spatial-tracker WS stream, `apartment_model` JSON, camera extrinsics, and every
  pipeline output speak THIS frame exclusively.

## App scene graph (three.js, Y-up)
- One conversion, in exactly one place: `apartmentRoot.rotation.x = -π/2` in
  `engine.js`. Children's geometry stays in apartment-frame (Z-up) object space.
- Consequence for shaders: in object space, **up is +Z** — the point-cloud shader's
  vertical shimmer acts on `position.z`, not `position.y`.
- Converting an apartment-frame point for scene use: let the root do it. Code that
  needs world-space (picking results, label projection) goes through
  `apartmentRoot.localToWorld()` / `worldToLocal()` — never hand-rolled.

## Cameras (OpenCV → three)
- Calibration produces OpenCV convention (+Z forward, +Y down), apartment frame.
- Conversion: `R_three = R_cv · diag(1, −1, −1)`, then compose with the apartmentRoot
  rotation. One helper (`rig.cameraPoseFromExtrinsics`), unit-tested with known points.
- fovY = `2·atan(H / (2·fy))`. Principal-point offset handled at the video-overlay
  layer (CSS shift) and/or `camera.setViewOffset`.

## Asset conventions
- `points.ply`: header comment `homeapt-points v1 zup metric` — x,y,z float32 +
  intensity uint8, little-endian, shuffled record order (progressive load looks
  uniform). Parser: `home-3d/pointcloud.js` (depends on EXACTLY this layout).
- `mesh.glb` / `collision.glb`: apartment-frame (Z-up) coordinates inside a GLB
  container — intentionally NOT glTF-convention Y-up; the apartmentRoot converts.
- `floor.json`: `walkable_polygon` + `holes`, apartment frame.

Debug: edit mode renders an XYZ axes gizmo (X red, Y green, Z blue — Z must point at
the ceiling when the root conversion is correct).
