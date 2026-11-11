---
title: Add the live den view's local loop, with people markers on this PC
target: internal
type: added
---

Adds step 3 of the live den view to `tools/den-live/`: `run_local.py`, one paste that shows each person in the den as a live marker on the owner's scan, on this PC only.
- A frame source fetches the den camera's newest bundle about 5 times a second, with den-take's own client.
- A detector process in the detector's venv, with no network, runs YOLO26s and RTMPose-m as the den simulation does.
- den-observe and den-track, unedited at their pins, turn each frame into floor tracks on step 2's registration. The tracker's floor box is the scan's walkable floor padded 1 m, not den-track's tape rectangle, which split a seated reader in the den simulation.
- Frame times come from arrival on this PC, because the den camera host's clock is wrong. Frames without the camera's depth correction are refused.
- A people stream (`den-live-people-1`) serves on 127.0.0.1. A local page serves Home from the `claude/den-live-home` branch, with its new people layer.
- The scan page and the live page now share one loopback harness, `home_harness.mjs`.
- Its tests replay the den simulation's rendered takes, and one runs the whole loop end to end through a fake camera.
