---
title: den-live pulls the camera's slim bundle on one kept connection
target: backend
type: changed
---

den-live's frame source asks the den camera for `/frame-slim.npz` (`den-camera-slim-1`) on one connection kept open, with If-None-Match and 304 while the frame is unchanged. It no longer pays a new TCP connection and slow start for every frame of about 770 kB. Offline, on the measured link, that moves the fetch from about 0.5 s to about 0.14 s (p95 under 0.2 s) and the input from about 2 to about 4.3 frames a second. Every reader gets the same depth, RGB and metadata. A camera without the slim path is pulled as before, and asked again a minute later.
