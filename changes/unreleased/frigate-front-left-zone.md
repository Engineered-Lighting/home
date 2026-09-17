---
title: Move the living-room front_left Frigate zone away from the sofa
target: backend
type: fixed
---

The `front_left` zone's sofa-facing edge sat 81 px from the sofa polygon, so a person on the sofa stretching their legs moved their detection box's bottom-centre into `front_left` and flipped that zone to present. The live Frigate config now uses `0.051,1,0.257,0.899,0.34,0.86,0.46,1` (nearest gap 155 px); the tracked example config is updated to match. The lighting fix that makes a movie keep every living-room light off regardless of zone flicker follows separately.
