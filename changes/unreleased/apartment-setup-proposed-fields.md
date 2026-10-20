---
title: Proposed Setup fields for room labels, hand-placed cameras and mmWave sensors (off by default)
target: desktop
type: added
---

Setup › Names now shows "Was …" with Reset, flags duplicate names without blocking them, and highlights or finds a light in the room while you edit it. Behind an opt-in proposal flag that stays off by default until the owner approves the proposed fields, Names also gets a short room label, and Cameras and sensors gets a hand placer and an ordered mmWave sensor. The hand placer sets the mount, a "point at a spot" aim, pan, tilt, lens and height, plus a coverage frustum and a Covers list. It never changes camera calibration. The mmWave sensor reads "Ordered · not connected" until Home Assistant has its entities. The proposal is in docs/design/apartment-unified/setup-stored-fields-proposal.md.
