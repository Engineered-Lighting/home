---
title: Repair the camera-moved check inputs for the desk-spotlight preview
target: backend
type: fixed
---

The camera-moved (mount) check no longer mistakes people for a moved room. Before comparing, it now ignores the depth pixels of anyone the camera sees in that frame. It can also use a new reference signature taken with nobody near the camera, once the owner has reviewed it. The check's limits are unchanged. The development preview still skips this check (owner decision); the repair applies to the setup verification and the strict path. Added an offline risk analysis with its tools, and a tool that builds the empty-room reference from a few frames. No camera was read and nothing was deployed.
