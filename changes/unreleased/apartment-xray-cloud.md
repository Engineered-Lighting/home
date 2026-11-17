---
title: Apartment Cloud view drawn as an x-ray scan, with a dollhouse cut
target: desktop
type: changed
---

The Apartment's Cloud view now draws the scan like a laser-scan render: faint points add up into translucent light,
so furniture and rooms read as layered glass and the far rooms show through the near ones. Walls between the camera
and a room drop to a low stub from any angle, using the scan's own floor plan. It applies on desktop WebGL once the
scan has loaded; phones and the WebGPU renderer keep the classic cloud, and the Lighting view draws the cloud as
before. Set localStorage `apartment3d.pointLook` to `classic` to keep the classic cloud in a browser.
