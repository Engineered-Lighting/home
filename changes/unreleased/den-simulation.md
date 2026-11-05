---
title: Add a loopback den simulation that runs the pipeline tools on a scripted scene
target: internal
type: added
---

Adds `tools/den-sim`, a local harness that serves the unchanged Home on `127.0.0.1` and feeds it a scripted scene. Each click runs the pinned observe, track, bundle and den-core tools, then the real lighting brain under a fake clock, and the virtual spotlight swings to the reader's hand point. The page then shows the exact fixture command on a fictional profile, marked not sent. No inference runs and nothing looks at a picture. The page, harness and tools have no route to a bulb, the camera, the Jetson, LA or Home Assistant, and a browser test checks this.
