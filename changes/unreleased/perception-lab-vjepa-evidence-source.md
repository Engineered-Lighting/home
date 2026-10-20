---
title: Add the V-JEPA 2.1 den evidence source and its on/off Lab experiment
target: backend
type: added
---

The Perception Lab can now feed JEV uncalibrated V-JEPA 2.1 posture, reading,
zero-shot, motion and scene-change buckets from a pinned, per-session LA worker
(own lifecycle supervisor on 192.168.0.100:9447, GPU admission, fail-closed
lease taken only after the detector pair is ready and only while the den is
occupied), as one shared, never-visual record in both the live packet and the
shadow facts wire. A new `vjepa-onoff` experiment withholds it from arm A and
grants it to arm B on placement-best's pipeline; it runs only with the
`vjepa_live` block provisioned and the household `vjepa-den-1` grant, which is
also its per-tick kill switch. Not yet deployed.
