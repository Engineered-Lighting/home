---
title: Add den-observe, the rung 2 writer of room-frame Observations
target: internal
type: added
---

Adds `tools/den-observe`, which turns detector rows and R's den takes into the live architecture's Observation records in the room frame, with covariance, depth timing, a declared plane fallback and freshness, and replays a take offline with a fake detector. It reads the `room-model.jsonl` that `tools/den-register` writes, taking its last revision. It runs no camera, detector, GPU or light, refuses a missing, stale or invalidated camera registration or a take older than it, and refuses a scored run until the owner closes its open slots and rung 1's mount check has confirmed the registration on that take or a later one.
