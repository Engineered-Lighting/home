---
title: The den camera turns its infrared flood on in the dark and serves its mono picture to den-live
target: backend
type: added
---

A new den camera release, `pilot-20261114-night`, switches to night when the
room is too dark for its colour camera (den-live's own rule: the picture's mean
brightness under 40 for 3 seconds, back over 60 for 3 seconds). At night it
lights the room with its infrared flood and, to den-live only, adds its
infrared-sensitive mono picture to the slim bundle as `den-camera-slim-2`.
Everything else it serves, `/frame.npz` and `den-camera-slim-1` included, is
unchanged, and `--night off` keeps the infrared light off. A night probe
(`tools/den-camera/night_probe.py`) measures the flood, the dot projector and
colour exposure settings as private takes.
