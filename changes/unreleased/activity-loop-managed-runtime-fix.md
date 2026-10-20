---
title: Den activity loop reads pose, position and camera from the live recording
target: backend
type: fixed
---

On the managed Jetson controller, the always-on den activity loop now reads
camera frames, approximate pose and person position from the recording it is
actually running. Before this fix it looked in the wrong place, so camera health
showed false and the lighting brain received no `person_pose` or
`person_position` events, while JEV activity still arrived. The reduced-cadence
back-off now also reaches each new rolled recording.
