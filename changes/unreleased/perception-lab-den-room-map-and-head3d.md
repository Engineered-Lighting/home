---
title: Perception Lab den room-mapping tools and a 3D head-direction prototype
target: backend
type: added
---

A new operator CLI, `room_mapper.py`, maps the Victoria den for plan section 11. It runs on the Jetson and reads the local camera owner only. It turns a Scaniverse LiDAR scan (GLB/OBJ) into the Apartment frame, `z_up_metric_floor0`, using the same floor convention as the Apartment pipeline. It then registers the OAK camera to that mesh and refuses a fit above 3 cm RMS or below 50% inliers.

It builds a medianed depth snapshot and fits the floor to get camera height and tilt. The point-to-plane ICP needs only numpy. Named 3D anchors (monitor, desk, painting and so on) come from points clicked on the mesh in a local browser labeller (`tools/room-map-labeler/index.html`). Fallbacks are an RGB pixel or box, owner pointing through the Lab's wrist keypoints, and tape offsets. The CLI saves a versioned site map with a revision and SHA. `room_context` now keeps the anchors' `category`, `shape`, `radius_m` and `size_m`.

`head3d.py` is a pure prototype. It lifts COCO17 face joints into stereo depth, fits a head template (falling back to the calibrated 2D yaw/pitch), and casts a head-pose cone against the anchors to give `looking_at` and its stability. `head3d_eval.py` scores it live, or on recorded sessions once they include depth bundles; none do yet.

The owner session is described in `docs/DEN-ROOM-MAPPING-RUNBOOK.md`. Nothing is wired into JEV or added to the Lab release, and scans, snapshots and maps stay in private local paths.
