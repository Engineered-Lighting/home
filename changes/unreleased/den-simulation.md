---
title: Add a loopback den simulation that runs the pipeline tools on rendered takes
target: internal
type: added
---

Adds `tools/den-sim`, a local harness that serves the unchanged Home on `127.0.0.1`. A rendered, artificial person is recorded once through the real take, check and camera-registration tools, and a real detector, the Lab's YOLO26s and RTMPose-m workers run by the new `tools/den-detect` on this PC's CPU, records its rows beside each take, checked again against the model before the set is used. Each click re-checks that take and its rows by hash, runs the pinned observe, track, bundle and need tools on the rows, and the older lighting brain swings a virtual spotlight, with the fixture command shown on a fictional profile and marked not sent. The panel draws the detector's box and joints on the take's own frame. Each hop's word comes from a receipt the click writes, and the top line says plainly that the chain is not yet connected start to finish: no model runs on a click, Qwen's answers are still typed, and nothing has a route to a bulb, the camera, the Jetson, the inference host or Home Assistant. Its data lives under `%USERPROFILE%\den-sim`, and the detector's venv under `%USERPROFILE%\ml\den-detector`.
