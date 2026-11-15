---
title: den-live shows the camera's own wait and the whole capture-to-stream time
target: backend
type: added
---

den-live's latency figures started when the camera packaged a frame, so they left out the camera's wait from the frame to its packaging: about 0.1 s at night, and 0.56–0.68 s on daylight afternoons. With the camera's body5 release, each event carries that wait as `camera_age_s`, and the health line adds its median and p95 over the last minute plus a capture-to-stream figure (the wait, the transit and den-live's own time); with an older camera these are null and everything reads as before.
