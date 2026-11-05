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
that later Observations' `transform_id` resolves to. A mount check invalidates
the entry on a large depth change or when a re-clicked mark is more than 5 cm
off. Only re-clicked marks can confirm a registration, because depth alone
cannot see a small turn or slide. Nothing is recorded, run or deployed by this
change.
