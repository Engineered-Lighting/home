---
title: The den camera serves a slim, kept-open bundle for den-live
target: backend
type: added
---

A new camera release, `pilot-20261114-slim`, adds `/frame-slim.npz` (`den-camera-slim-1`): the newest frame's depth, RGB and full metadata without the stereo images den-live never reads, about half the bytes, losslessly. It's ready about 90 ms sooner, kept on one open connection, and answered with 304 while the frame is unchanged. `/frame.npz` and every other path answer exactly as before, and perception-lab's camera pin is unchanged.
