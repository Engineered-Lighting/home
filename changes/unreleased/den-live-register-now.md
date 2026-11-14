---
title: Add the den camera's re-registration to the owner's scan
target: internal
type: added
---

Adds `tools/den-live/register_now.py`, step 2 of the live den view, which the owner runs as one paste on the workstation.
- It records a 20 s take of the empty den with den-take's own client and writer, into a private folder outside every checkout.
- It checks that every frame's `depth_correction` record says the camera service corrected the depth at its source. It stops if any frame doesn't. It applies no depth factor of its own.
- A floor guard fits the floor plane in that depth and stops before any clicking if the lens height is more than 5% off the tape.
- The owner clicks six features on den-register's own click page, then the same six in the scan on `scan_pick.mjs`. That's a loopback page running Home's unchanged code and its display-mesh picker.
- den-register, unedited at its pin, fits with the new method file `REGISTER-LIVE-20261111.md`. The gate is 0.10 m, every open slot is closed at its proposal, and its records say `scan-pick + fit`.
- After the fit, it prints the features' own similarity scale as a cross-check. On a pass, the scan page shows where the fit put the camera.
- Its tests run end to end on the den simulation's rendered marks take, through a fake camera and the real scan page on the authored room, with no camera, inference host or ssh.
