---
title: Den camera light scale from a grey card and a lux meter (rung 7)
target: internal
type: added
---

`tools/den-photometry` adds rung 7's measurement code for experiment E8: a
written method (`METHOD-V1.md`) with the owner's open slots, and a PC tool
that captures a grey card under rung 8's metering hold and meter-norm/v1,
pairs each lux reading typed into a worksheet with its frames, fits the camera
scale from one card and one reading, and checks the known-card round trip,
exposure invariance, held-out points in lux and card-relative reflectance,
each with its uncertainty. It writes CalibrationRecord, RoomModel and
LightState records, never writes to a light, and refuses to run while the
meter tolerance or any other slot is still open. Nothing runs until the owner
approves; frames stay on the PC.
