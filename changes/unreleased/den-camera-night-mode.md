---
title: The den camera can serve its mono picture to den-live in the dark
target: backend
type: added
---

A new den camera release, `pilot-20261114-night`, can switch to night when the
room is too dark for its colour camera (den-live's own rule: the picture's mean
brightness under 6 for 3 seconds, back over 9 for 3 seconds). At night it adds
its infrared-sensitive mono picture, to den-live only, to the slim bundle as
`den-camera-slim-2`. It's off unless the service is started with
`--night auto`, and its infrared light stays off: on this camera the flood and
the dot projector reach only one of the two mono cameras, which makes depth
worse. Everything else it serves, `/frame.npz` and `den-camera-slim-1`
included, is unchanged. A night probe
(`tools/den-camera/night_probe.py`) measures the flood, the dot projector and
colour exposure settings as private takes.
