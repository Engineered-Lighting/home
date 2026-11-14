---
title: Stop the Den detector dropping about one frame in sixty
target: backend
type: fixed
---

den-live no longer throws away a camera frame because its detection row failed den-observe's checks. There were two causes. About one frame in twenty-five had a capture time a few hundredths of a second after its arrival, because of how the camera's clock is lined up with the server's, so the row looked ready before it was taken. And a busy frame could have more than the 64 detections a row may hold. Each lost frame briefly emptied the live view. A row is now never marked ready before its capture, and a busy frame keeps its people first, up to 64 boxes. Boxes are also clipped into the frame as a guard. The detector's log says once a minute how often either guard was needed, and why a row was refused if one ever is.
