---
title: Den camera registration from measured control points (rung 1)
target: internal
type: added
---

`tools/den-register` places the den camera in the room frame from control
points the owner measures with a tape and clicks on one frame of an R take, on
a page served only on the owner's PC. It fits a rigid transform, accepts it
only with three fit points and two independent checks all within 5 cm, names
every refusal, and writes the RoomModel camera entry and a CalibrationRecord
that later Observations' `transform_id` resolves to. The entry holds the RGB
geometry rung 2 reads, taken from the frame and from a saved copy of the
camera's `/calibration` reply. A mount check invalidates
the entry on a large depth change or when a re-clicked mark is more than 5 cm
off. Only every mark re-clicked on a later take can confirm a registration,
with the movement they show held to 5 cm across the whole view, because depth
alone cannot see a small turn or slide. A confirmation is saved in the RoomModel
for rung 2 to read. Nothing is recorded, run or deployed by this change.
